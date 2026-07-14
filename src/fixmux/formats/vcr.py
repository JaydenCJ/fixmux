"""VCR cassette codec — both the Ruby VCR and Python vcrpy dialects.

The two dialects share a shape but differ in every detail that matters:

===================  =========================  ===========================
                     ``vcr`` (Python vcrpy)     ``vcr-ruby`` (Ruby VCR)
===================  =========================  ===========================
Root key             ``interactions``           ``http_interactions``
Method casing        upper (``GET``)            lower (``get``)
Request body         plain string / null        ``{encoding, string}``
Binary bodies        YAML ``!!binary``          ``base64_string`` field
Timestamps           none                       ``recorded_at`` (RFC 2822)
Document start       none                       ``---``
===================  =========================  ===========================

Cassettes are read from YAML (via :mod:`fixmux.yamlite`) or JSON — vcrpy's
JSON serializer is detected automatically. On output, ``serializer="yaml"``
(the default both libraries record with) or ``serializer="json"`` selects
the encoding.
"""

from __future__ import annotations

import base64
import json
import re
from typing import Any, List, Optional

from .. import model, yamlite
from ..errors import ParseError, UnsupportedFeatureError
from ..model import Body, Exchange, Fixture, Request, Response

FORMAT_PYTHON = "vcr"
FORMAT_RUBY = "vcr-ruby"

_RUBY_KEY = "http_interactions"
_PYTHON_KEY = "interactions"
_YAML_RUBY_RE = re.compile(r"^http_interactions:", re.MULTILINE)
_YAML_PYTHON_RE = re.compile(r"^interactions:", re.MULTILINE)


def detect(text: str, data: Any) -> Optional[str]:
    if isinstance(data, dict):
        if isinstance(data.get(_RUBY_KEY), list):
            return FORMAT_RUBY
        if isinstance(data.get(_PYTHON_KEY), list):
            return FORMAT_PYTHON
        return None
    if _YAML_RUBY_RE.search(text):
        return FORMAT_RUBY
    if _YAML_PYTHON_RE.search(text):
        return FORMAT_PYTHON
    return None


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load(text: str, notes: List[str], base_url: Optional[str] = None) -> Fixture:
    data = _parse_document(text)
    if not isinstance(data, dict):
        raise ParseError("vcr: cassette root must be a mapping")
    if _RUBY_KEY in data:
        dialect, interactions = "ruby", data[_RUBY_KEY]
    elif _PYTHON_KEY in data:
        dialect, interactions = "python", data[_PYTHON_KEY]
    else:
        raise ParseError("vcr: no interactions/http_interactions key")
    if not isinstance(interactions, list):
        raise ParseError("vcr: interactions must be a sequence")
    fixture = Fixture(
        meta={"source_format": FORMAT_RUBY if dialect == "ruby" else FORMAT_PYTHON}
    )
    if isinstance(data.get("recorded_with"), str):
        fixture.meta["recorded_with"] = data["recorded_with"]
    for index, interaction in enumerate(interactions):
        if not isinstance(interaction, dict):
            raise ParseError("vcr: interaction %d is not a mapping" % index)
        fixture.exchanges.append(_load_interaction(interaction, index))
    return fixture


def _parse_document(text: str) -> Any:
    stripped = text.lstrip()
    if stripped.startswith(("{", "[")):
        try:
            return json.loads(text)
        except ValueError as exc:
            raise ParseError("vcr: invalid JSON cassette: %s" % exc)
    return yamlite.loads(text)


def _load_interaction(interaction: dict, index: int) -> Exchange:
    req = interaction.get("request") or {}
    res = interaction.get("response") or {}
    uri = req.get("uri")
    if not uri:
        raise ParseError("vcr: interaction %d has no request.uri" % index)
    request_headers = _load_headers(req.get("headers"))
    response_headers = _load_headers(res.get("headers"))
    status = res.get("status") or {}
    if isinstance(status, int):  # tolerated: a bare status code
        status = {"code": status}
    exchange = Exchange(
        request=Request(
            method=str(req.get("method") or "GET").upper(),
            url=str(uri),
            headers=request_headers,
            body=_load_body(req.get("body"), model.first_header(request_headers, "Content-Type")),
        ),
        response=Response(
            status=int(status.get("code") or 0),
            status_text=str(status.get("message") or ""),
            headers=response_headers,
            body=_load_body(res.get("body"), model.first_header(response_headers, "Content-Type")),
        ),
    )
    recorded_at = interaction.get("recorded_at")
    if isinstance(recorded_at, str):
        parsed = model.parse_when(recorded_at)
        if parsed:
            exchange.meta["recorded_at"] = parsed
        else:
            exchange.meta["recorded_at_raw"] = recorded_at
    return exchange


def _load_headers(raw: Any) -> model.Headers:
    headers: model.Headers = []
    if not isinstance(raw, dict):
        return headers
    for name, values in raw.items():
        if isinstance(values, (list, tuple)):
            for value in values:
                headers.append((str(name), str(value)))
        else:
            headers.append((str(name), str(values)))
    return headers


def _load_body(raw: Any, mime: Optional[str]) -> Body:
    if raw is None:
        return Body(mime=mime)
    if isinstance(raw, bytes):  # vcrpy: body was a YAML !!binary scalar
        return Body(base64=base64.b64encode(raw).decode("ascii"), mime=mime)
    if isinstance(raw, str):
        return Body(text=raw, mime=mime) if raw else Body(mime=mime)
    if isinstance(raw, dict):
        if raw.get("base64_string"):
            payload = re.sub(r"\s+", "", str(raw["base64_string"]))
            return Body(base64=payload, mime=mime)
        inner = raw.get("string")
        if inner is None:
            return Body(mime=mime)
        if isinstance(inner, bytes):
            return Body(base64=base64.b64encode(inner).decode("ascii"), mime=mime)
        return Body(text=str(inner), mime=mime) if inner != "" else Body(mime=mime)
    raise ParseError("vcr: unsupported body value of type %s" % type(raw).__name__)


# ---------------------------------------------------------------------------
# Dumping
# ---------------------------------------------------------------------------


def dump(
    fixture: Fixture,
    notes: List[str],
    strict: bool = False,
    dialect: str = "python",
    serializer: str = "yaml",
    **options: Any,
) -> str:
    if serializer not in ("yaml", "json"):
        raise ValueError("vcr serializer must be 'yaml' or 'json'")
    interactions = [
        _dump_interaction(exchange, dialect, serializer, notes, strict)
        for exchange in fixture.exchanges
    ]
    if dialect == "ruby":
        from .. import __version__

        document: dict = {
            _RUBY_KEY: interactions,
            "recorded_with": fixture.meta.get("recorded_with") or "fixmux %s" % __version__,
        }
    else:
        document = {_PYTHON_KEY: interactions, "version": 1}
    if serializer == "json":
        return json.dumps(document, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    return yamlite.dumps(document, sort_keys=True, explicit_start=(dialect == "ruby"))


def _dump_interaction(
    exchange: Exchange, dialect: str, serializer: str, notes: List[str], strict: bool
) -> dict:
    request = exchange.request
    response = exchange.response
    method = request.method.lower() if dialect == "ruby" else request.method.upper()
    interaction: dict = {
        "request": {
            "method": method,
            "uri": request.url,
            "headers": _dump_headers(request.headers),
            "body": _dump_body(request.body, dialect, serializer, notes, strict, "request"),
        },
        "response": {
            "status": {"code": response.status, "message": response.status_text},
            "headers": _dump_headers(response.headers),
            "body": _dump_body(response.body, dialect, serializer, notes, strict, "response"),
        },
    }
    if dialect == "ruby":
        interaction["recorded_at"] = model.to_rfc2822(exchange.meta)
    return interaction


def _dump_headers(headers: model.Headers) -> dict:
    return model.header_multimap(headers)


def _dump_body(
    body: Body, dialect: str, serializer: str, notes: List[str], strict: bool, side: str
) -> Any:
    if dialect == "ruby":
        if body.base64 is not None:
            return {"encoding": "ASCII-8BIT", "base64_string": body.base64}
        return {"encoding": "UTF-8", "string": body.text or ""}
    # vcrpy: request bodies are bare strings, response bodies are wrapped.
    if body.base64 is not None:
        if serializer == "yaml":
            raw = body.to_bytes()
            return raw if side == "request" else {"string": raw}
        message = "vcr: the JSON serializer cannot represent binary bodies"
        if strict:
            raise UnsupportedFeatureError(message)
        notes.append(message + "; wrote a base64_string field instead")
        return {"base64_string": body.base64}
    if side == "request":
        return body.text if body.text else None
    return {"string": body.text or ""}
