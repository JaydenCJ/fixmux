"""nock definition codec (the ``nock.recorder`` / ``nock.define`` format).

A nock fixture is a JSON array of definition objects, each with ``scope``
(origin), ``path`` (path + query), ``method``, ``status``, a request
``body``, and a ``response`` that is either a string, a parsed JSON value,
or — when ``responseIsBinary`` is set — a hex string, matching what
``nock.recorder.rec({output_objects: true})`` writes and ``nock.define``
consumes.

JSON responses are stored parsed (as nock records them) and re-serialized
with sorted keys on load, so the comparison layer treats them by value, not
by byte. Response headers use ``rawHeaders`` (a flat name/value array) to
preserve duplicates and ordering exactly.
"""

from __future__ import annotations

import base64
import json
from typing import Any, List, Optional

from .. import model
from ..errors import ParseError, UnsupportedFeatureError
from ..model import Body, Exchange, Fixture, Request, Response

FORMAT_ID = "nock"


def detect(text: str, data: Any) -> Optional[str]:
    if isinstance(data, dict) and "scope" in data and "method" in data:
        data = [data]
    if isinstance(data, list) and data:
        if all(isinstance(item, dict) and "scope" in item and "path" in item for item in data):
            return FORMAT_ID
    return None


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load(text: str, notes: List[str], base_url: Optional[str] = None) -> Fixture:
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ParseError("nock: invalid JSON: %s" % exc)
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise ParseError("nock: expected an array of definitions")
    fixture = Fixture(meta={"source_format": FORMAT_ID})
    for index, definition in enumerate(data):
        if not isinstance(definition, dict):
            raise ParseError("nock: definition %d is not an object" % index)
        fixture.exchanges.append(_load_definition(definition, index, notes))
    return fixture


def _load_definition(definition: dict, index: int, notes: List[str]) -> Exchange:
    scope = definition.get("scope")
    if not scope:
        raise ParseError("nock: definition %d has no scope" % index)
    path = str(definition.get("path") or "/")
    if not path.startswith("/"):
        path = "/" + path
    request_headers = _load_dict_headers(definition.get("reqheaders"))
    response_headers = _load_response_headers(definition)
    exchange = Exchange(
        request=Request(
            method=str(definition.get("method") or "GET").upper(),
            url=str(scope).rstrip("/") + path,
            headers=request_headers,
            body=_load_request_body(
                definition.get("body"), model.first_header(request_headers, "Content-Type")
            ),
        ),
        response=Response(
            status=int(definition.get("status") or 200),
            status_text="",
            headers=response_headers,
            body=_load_response_body(definition, response_headers),
        ),
    )
    return exchange


def _load_dict_headers(raw: Any) -> model.Headers:
    headers: model.Headers = []
    if not isinstance(raw, dict):
        return headers
    for name, value in raw.items():
        if isinstance(value, (list, tuple)):
            for item in value:
                headers.append((str(name), str(item)))
        else:
            headers.append((str(name), str(value)))
    return headers


def _load_response_headers(definition: dict) -> model.Headers:
    raw_headers = definition.get("rawHeaders")
    if isinstance(raw_headers, list):
        if len(raw_headers) % 2:
            raise ParseError("nock: rawHeaders must hold name/value pairs")
        return [
            (str(raw_headers[i]), str(raw_headers[i + 1]))
            for i in range(0, len(raw_headers), 2)
        ]
    return _load_dict_headers(definition.get("headers"))


def _load_request_body(raw: Any, mime: Optional[str]) -> Body:
    if raw is None or raw == "":
        return Body(mime=mime)
    if isinstance(raw, str):
        return Body(text=raw, mime=mime)
    return Body(
        text=json.dumps(raw, ensure_ascii=False, sort_keys=True),
        mime=mime or "application/json",
    )


def _load_response_body(definition: dict, headers: model.Headers) -> Body:
    raw = definition.get("response")
    mime = model.first_header(headers, "Content-Type")
    if definition.get("responseIsBinary"):
        if not isinstance(raw, str):
            raise ParseError("nock: responseIsBinary set but response is not a hex string")
        try:
            payload = bytes.fromhex(raw)
        except ValueError:
            raise ParseError("nock: responseIsBinary set but response is not valid hex")
        return Body(base64=base64.b64encode(payload).decode("ascii"), mime=mime)
    if raw is None or raw == "":
        return Body(mime=mime)
    if isinstance(raw, str):
        return Body(text=raw, mime=mime)
    return Body(
        text=json.dumps(raw, ensure_ascii=False, sort_keys=True),
        mime=mime or "application/json",
    )


# ---------------------------------------------------------------------------
# Dumping
# ---------------------------------------------------------------------------


def dump(fixture: Fixture, notes: List[str], strict: bool = False, **options: Any) -> str:
    definitions = []
    for index, exchange in enumerate(fixture.exchanges):
        if exchange.request.body.base64 is not None:
            message = (
                "nock: definition %d has a binary request body; nock has no "
                "binary flag for request bodies, wrote hex" % index
            )
            if strict:
                raise UnsupportedFeatureError(message)
            notes.append(message)
        definitions.append(_dump_definition(exchange))
    return json.dumps(definitions, indent=2, ensure_ascii=False, sort_keys=False) + "\n"


def _dump_definition(exchange: Exchange) -> dict:
    request = exchange.request
    response = exchange.response
    definition: dict = {
        "scope": model.origin(request.url, explicit_port=True),
        "method": request.method,
        "path": model.path_and_query(request.url),
        "body": _dump_request_body(request.body),
        "status": response.status,
    }
    body = response.body
    if body.base64 is not None:
        definition["response"] = body.to_bytes().hex()
        definition["responseIsBinary"] = True
    elif body.text is not None and _is_json_mime(body.mime):
        parsed = body.json()
        definition["response"] = parsed if parsed is not None else body.text
    else:
        definition["response"] = body.text or ""
    if request.headers:
        definition["reqheaders"] = _dump_multimap(request.headers)
    if response.headers:
        raw: List[str] = []
        for name, value in response.headers:
            raw.extend([name, value])
        definition["rawHeaders"] = raw
    return definition


def _dump_request_body(body: Body) -> Any:
    if body.is_empty:
        return ""
    if body.text is not None:
        if _is_json_mime(body.mime):
            parsed = body.json()
            if parsed is not None:
                return parsed
        return body.text
    # nock request bodies are strings; hex is only defined for responses.
    return base64.b64decode(body.base64 or "").hex()


def _dump_multimap(headers: model.Headers) -> dict:
    out: dict = {}
    for name, values in model.header_multimap(headers).items():
        out[name] = values[0] if len(values) == 1 else values
    return out


def _is_json_mime(mime: Optional[str]) -> bool:
    if not mime:
        return False
    essence = mime.split(";", 1)[0].strip().lower()
    return essence == "application/json" or essence.endswith("+json")
