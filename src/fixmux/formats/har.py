"""HAR 1.2 codec (HTTP Archive, the DevTools export format).

HAR is the richest of the four dialects — it records timings, cookies,
cache state, and page groupings that no replay-fixture format wants. fixmux
maps the exchange core (method, URL, headers, bodies, status, start time)
and reports anything it drops as a note, so ``--strict`` conversions can
refuse instead of silently losing data.

Output is a minimal but spec-valid HAR: required fields are present with
the spec's "unknown" sentinels (``-1`` sizes, zero timings), ``creator``
names fixmux, and entries carry a deterministic epoch ``startedDateTime``
when the source format recorded no timestamp.
"""

from __future__ import annotations

import json
from typing import Any, List, Optional

from .. import model
from ..errors import ParseError
from ..model import Body, Exchange, Fixture, Request, Response

FORMAT_ID = "har"

_LOSSY_ENTRY_FIELDS = ("cache", "timings", "time", "serverIPAddress", "connection", "pageref")


def detect(text: str, data: Any) -> Optional[str]:
    if isinstance(data, dict) and isinstance(data.get("log"), dict):
        if isinstance(data["log"].get("entries"), list):
            return FORMAT_ID
    return None


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load(text: str, notes: List[str], base_url: Optional[str] = None) -> Fixture:
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ParseError("har: invalid JSON: %s" % exc)
    log = data.get("log") if isinstance(data, dict) else None
    if not isinstance(log, dict) or not isinstance(log.get("entries"), list):
        raise ParseError("har: missing log.entries")
    fixture = Fixture(meta={"source_format": FORMAT_ID})
    creator = log.get("creator")
    if isinstance(creator, dict) and creator.get("name"):
        fixture.meta["recorded_with"] = "%s %s" % (
            creator.get("name"),
            creator.get("version", ""),
        )
        fixture.meta["recorded_with"] = fixture.meta["recorded_with"].strip()
    dropped: "set[str]" = set()
    for index, entry in enumerate(log["entries"]):
        if not isinstance(entry, dict):
            raise ParseError("har: entry %d is not an object" % index)
        fixture.exchanges.append(_load_entry(entry, index, dropped))
    if isinstance(log.get("pages"), list) and log["pages"]:
        dropped.add("pages")
    if dropped:
        notes.append(
            "har: ignored HAR-only fields with no exchange equivalent: %s"
            % ", ".join(sorted(dropped))
        )
    return fixture


def _load_entry(entry: dict, index: int, dropped: "set[str]") -> Exchange:
    req = entry.get("request") or {}
    res = entry.get("response") or {}
    if not req.get("url"):
        raise ParseError("har: entry %d has no request.url" % index)
    exchange = Exchange(
        request=Request(
            method=(req.get("method") or "GET").upper(),
            url=req["url"],
            headers=_load_headers(req.get("headers")),
            body=_load_request_body(req.get("postData")),
        ),
        response=Response(
            status=int(res.get("status") or 0),
            status_text=res.get("statusText") or "",
            headers=_load_headers(res.get("headers")),
            body=_load_content(res.get("content")),
        ),
    )
    started = entry.get("startedDateTime")
    if started:
        parsed = model.parse_when(str(started))
        if parsed:
            exchange.meta["recorded_at"] = parsed
    for field in _LOSSY_ENTRY_FIELDS:
        value = entry.get(field)
        if value not in (None, {}, [], "", -1, 0):
            dropped.add(field)
    return exchange


def _load_headers(raw: Any) -> model.Headers:
    headers: model.Headers = []
    for item in raw or []:
        if isinstance(item, dict) and "name" in item:
            headers.append((str(item["name"]), str(item.get("value", ""))))
    return headers


def _load_request_body(post: Any) -> Body:
    if not isinstance(post, dict):
        return Body()
    mime = post.get("mimeType") or None
    text = post.get("text")
    if text is not None:
        if post.get("_fixmuxEncoding") == "base64":
            return Body(base64=str(text), mime=mime)
        return Body(text=str(text), mime=mime)
    params = post.get("params")
    if isinstance(params, list) and params:
        from urllib.parse import urlencode

        pairs = [(p.get("name", ""), p.get("value", "")) for p in params if isinstance(p, dict)]
        return Body(text=urlencode(pairs), mime=mime or "application/x-www-form-urlencoded")
    return Body(mime=mime)


def _load_content(content: Any) -> Body:
    if not isinstance(content, dict):
        return Body()
    mime = content.get("mimeType") or None
    text = content.get("text")
    if text is None:
        return Body(mime=mime)
    if content.get("encoding") == "base64":
        return Body(base64=str(text), mime=mime)
    return Body(text=str(text), mime=mime)


# ---------------------------------------------------------------------------
# Dumping
# ---------------------------------------------------------------------------


def dump(fixture: Fixture, notes: List[str], strict: bool = False, **options: Any) -> str:
    entries = [_dump_entry(exchange) for exchange in fixture.exchanges]
    from .. import __version__

    log = {
        "log": {
            "version": "1.2",
            "creator": {"name": "fixmux", "version": __version__},
            "entries": entries,
        }
    }
    return json.dumps(log, indent=2, ensure_ascii=False, sort_keys=False) + "\n"


def _dump_entry(exchange: Exchange) -> dict:
    request = exchange.request
    response = exchange.response
    scheme, host, port, path, query = model.split_url(request.url)
    query_items = _query_string(query)
    entry = {
        "startedDateTime": model.to_iso(exchange.meta),
        "time": 0,
        "request": {
            "method": request.method,
            "url": request.url,
            "httpVersion": "HTTP/1.1",
            "cookies": [],
            "headers": _dump_headers(request.headers),
            "queryString": query_items,
            "headersSize": -1,
            "bodySize": -1,
        },
        "response": {
            "status": response.status,
            "statusText": response.status_text,
            "httpVersion": "HTTP/1.1",
            "cookies": [],
            "headers": _dump_headers(response.headers),
            "content": _dump_content(response.body),
            "redirectURL": model.first_header(response.headers, "Location") or "",
            "headersSize": -1,
            "bodySize": -1,
        },
        "cache": {},
        "timings": {"send": 0, "wait": 0, "receive": 0},
    }
    body = request.body
    if not body.is_empty:
        post: dict = {"mimeType": body.mime or "application/octet-stream"}
        if body.text is not None:
            post["text"] = body.text
        else:
            # HAR has no request-body encoding flag; keep the payload intact
            # by storing base64 plus a fixmux extension marker.
            post["text"] = body.base64
            post["_fixmuxEncoding"] = "base64"
        entry["request"]["postData"] = post
    return entry


def _dump_headers(headers: model.Headers) -> List[dict]:
    return [{"name": name, "value": value} for name, value in headers]


def _query_string(query: str) -> List[dict]:
    from urllib.parse import parse_qsl

    return [{"name": name, "value": value} for name, value in parse_qsl(query, keep_blank_values=True)]


def _dump_content(body: Body) -> dict:
    content: dict = {"mimeType": body.mime or ""}
    if body.base64 is not None:
        content["size"] = len(body.to_bytes())
        content["text"] = body.base64
        content["encoding"] = "base64"
    elif body.text is not None:
        content["size"] = len(body.text.encode("utf-8"))
        content["text"] = body.text
    else:
        content["size"] = 0
    return content
