"""WireMock stub-mapping codec.

Reads both shapes WireMock uses on disk: a single mapping file
(``{"request": …, "response": …}``) and the multi-stub export produced by
``__admin/mappings`` (``{"mappings": […]}``). Output is always the
multi-stub form for more than one exchange, and a single mapping otherwise
— exactly what ``wiremock --root-dir`` expects back.

Two structural mismatches with the other dialects are handled explicitly:

* **WireMock stubs have no host.** A stub matches whatever server it is
  mounted on, so the origin is dropped on output (with a note) and
  supplied via ``base_url`` on input (default ``http://127.0.0.1:8080``,
  WireMock's own default port).
* **WireMock matches, other formats record.** Only equality matchers
  (``url``, ``urlPath`` + ``equalTo`` query params, ``equalTo`` headers and
  ``equalTo``/``equalToJson`` bodies) convert losslessly. Regex or partial
  matchers (``urlPattern``, ``matches``, ``contains``…) cannot be recorded
  as concrete values, so reading keeps the pattern text (URLs) or drops the
  matcher (headers), surfacing every degradation as a note.
"""

from __future__ import annotations

import json
from typing import Any, List, Optional
from urllib.parse import urlencode

from .. import model
from ..errors import ParseError, UnsupportedFeatureError
from ..model import Body, Exchange, Fixture, Request, Response

FORMAT_ID = "wiremock"

DEFAULT_BASE_URL = "http://127.0.0.1:8080"


def detect(text: str, data: Any) -> Optional[str]:
    if not isinstance(data, dict):
        return None
    if isinstance(data.get("mappings"), list):
        items = data["mappings"]
        if all(isinstance(m, dict) and "request" in m and "response" in m for m in items):
            return FORMAT_ID if items else None
    if isinstance(data.get("request"), dict) and isinstance(data.get("response"), dict):
        request = data["request"]
        if any(key in request for key in ("url", "urlPath", "urlPattern", "urlPathPattern")):
            return FORMAT_ID
    return None


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load(text: str, notes: List[str], base_url: Optional[str] = None) -> Fixture:
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ParseError("wiremock: invalid JSON: %s" % exc)
    if isinstance(data, dict) and isinstance(data.get("mappings"), list):
        mappings = data["mappings"]
    elif isinstance(data, dict) and "request" in data and "response" in data:
        mappings = [data]
    else:
        raise ParseError("wiremock: expected a stub mapping or a mappings export")
    base = (base_url or DEFAULT_BASE_URL).rstrip("/")
    fixture = Fixture(meta={"source_format": FORMAT_ID})
    for index, mapping in enumerate(mappings):
        if not isinstance(mapping, dict):
            raise ParseError("wiremock: mapping %d is not an object" % index)
        fixture.exchanges.append(_load_mapping(mapping, index, base, notes))
    return fixture


def _load_mapping(mapping: dict, index: int, base: str, notes: List[str]) -> Exchange:
    req = mapping.get("request") or {}
    res = mapping.get("response") or {}
    path = _request_path(req, index, notes)
    headers = _load_match_headers(req.get("headers"), index, notes)
    body = _load_match_body(req.get("bodyPatterns"), index, notes)
    if body.mime is None:
        body.mime = model.first_header(headers, "Content-Type")
    return Exchange(
        request=Request(
            method=str(req.get("method") or "GET").upper(),
            url=base + path,
            headers=headers,
            body=body,
        ),
        response=_load_response(res),
    )


def _request_path(req: dict, index: int, notes: List[str]) -> str:
    if req.get("url"):
        return str(req["url"])
    if req.get("urlPath"):
        path = str(req["urlPath"])
        params = req.get("queryParameters")
        if isinstance(params, dict) and params:
            pairs = []
            for name, matcher in params.items():
                if isinstance(matcher, dict) and "equalTo" in matcher:
                    pairs.append((name, str(matcher["equalTo"])))
                else:
                    notes.append(
                        "wiremock: mapping %d query parameter %r uses a non-equality "
                        "matcher; dropped" % (index, name)
                    )
            if pairs:
                path += "?" + urlencode(pairs)
        return path
    for key in ("urlPattern", "urlPathPattern"):
        if req.get(key):
            notes.append(
                "wiremock: mapping %d matches URLs by regex (%s); kept the pattern "
                "text as the path" % (index, key)
            )
            return str(req[key])
    return "/"


def _load_match_headers(raw: Any, index: int, notes: List[str]) -> model.Headers:
    headers: model.Headers = []
    if not isinstance(raw, dict):
        return headers
    for name, matcher in raw.items():
        if isinstance(matcher, dict) and "equalTo" in matcher:
            headers.append((str(name), str(matcher["equalTo"])))
        else:
            notes.append(
                "wiremock: mapping %d header %r uses a non-equality matcher; dropped"
                % (index, name)
            )
    return headers


def _load_match_body(raw: Any, index: int, notes: List[str]) -> Body:
    if not isinstance(raw, list) or not raw:
        return Body()
    pattern = raw[0]
    if isinstance(pattern, dict):
        if "equalTo" in pattern:
            return Body(text=str(pattern["equalTo"]))
        if "binaryEqualTo" in pattern:
            return Body(base64=str(pattern["binaryEqualTo"]))
        if "equalToJson" in pattern:
            value = pattern["equalToJson"]
            text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
            return Body(text=text, mime="application/json")
    notes.append(
        "wiremock: mapping %d body pattern uses a non-equality matcher; dropped" % index
    )
    return Body()


def _load_response(res: dict) -> Response:
    headers: model.Headers = []
    raw_headers = res.get("headers")
    if isinstance(raw_headers, dict):
        for name, value in raw_headers.items():
            if isinstance(value, (list, tuple)):
                for item in value:
                    headers.append((str(name), str(item)))
            else:
                headers.append((str(name), str(value)))
    mime = model.first_header(headers, "Content-Type")
    if res.get("base64Body"):
        body = Body(base64=str(res["base64Body"]), mime=mime)
    elif "jsonBody" in res:
        body = Body(
            text=json.dumps(res["jsonBody"], ensure_ascii=False, sort_keys=True),
            mime=mime or "application/json",
        )
    elif res.get("body") is not None:
        body = Body(text=str(res["body"]), mime=mime)
    else:
        body = Body(mime=mime)
    return Response(
        status=int(res.get("status") or 200),
        status_text=str(res.get("statusMessage") or ""),
        headers=headers,
        body=body,
    )


# ---------------------------------------------------------------------------
# Dumping
# ---------------------------------------------------------------------------


def dump(fixture: Fixture, notes: List[str], strict: bool = False, **options: Any) -> str:
    mappings = []
    hosts = set()
    for index, exchange in enumerate(fixture.exchanges):
        mappings.append(_dump_mapping(exchange, index))
        if model.split_url(exchange.request.url)[1]:
            hosts.add(model.origin(exchange.request.url))
    if hosts:
        message = "wiremock: stub mappings carry no host; dropped origin(s) %s" % ", ".join(
            sorted(hosts)
        )
        if strict:
            raise UnsupportedFeatureError(message)
        notes.append(message)
    if len(mappings) == 1:
        document: Any = mappings[0]
    else:
        document = {"mappings": mappings}
    return json.dumps(document, indent=2, ensure_ascii=False, sort_keys=False) + "\n"


def _dump_mapping(exchange: Exchange, index: int) -> dict:
    request = exchange.request
    response = exchange.response
    req: dict = {
        "method": request.method,
        "url": model.path_and_query(request.url),
    }
    if request.headers:
        req["headers"] = {
            name: {"equalTo": ", ".join(values)}
            for name, values in model.header_multimap(request.headers).items()
        }
    if not request.body.is_empty:
        if request.body.text is not None:
            req["bodyPatterns"] = [{"equalTo": request.body.text}]
        else:
            req["bodyPatterns"] = [{"binaryEqualTo": request.body.base64}]
    res: dict = {"status": response.status}
    if response.status_text:
        res["statusMessage"] = response.status_text
    if response.headers:
        res["headers"] = _dump_response_headers(response.headers)
    if response.body.base64 is not None:
        res["base64Body"] = response.body.base64
    elif response.body.text is not None:
        res["body"] = response.body.text
    return {"name": "fixmux-%d" % index, "request": req, "response": res}


def _dump_response_headers(headers: model.Headers) -> dict:
    out: dict = {}
    for name, values in model.header_multimap(headers).items():
        out[name] = values[0] if len(values) == 1 else values
    return out
