"""Tests for the HAR 1.2 codec: loading DevTools-style captures and writing
spec-valid, deterministic archives back out."""

from __future__ import annotations

import base64
import json

from fixmux import load_fixture, dump_fixture
from fixmux.model import Body, Exchange, Fixture, Request, Response


def test_load_har_extracts_both_exchanges(har_text):
    fixture = load_fixture(har_text, "har")
    assert len(fixture.exchanges) == 2
    first = fixture.exchanges[0]
    assert first.request.method == "GET"
    assert first.request.url == "http://example.test/api/members?page=2"
    assert first.response.status == 200
    assert first.response.body.text == '{"members": ["aya", "ben"]}'


def test_load_har_preserves_duplicate_headers_in_order(har_text):
    fixture = load_fixture(har_text, "har")
    traces = [v for k, v in fixture.exchanges[0].request.headers if k == "X-Trace"]
    assert traces == ["one", "two"]


def test_load_har_keeps_timestamp_and_notes_dropped_fields(har_text):
    notes = []
    fixture = load_fixture(har_text, "har", notes=notes)
    assert fixture.exchanges[0].meta["recorded_at"] == "2026-03-01T09:30:00+00:00"
    assert any("timings" in note for note in notes)


def test_load_har_decodes_base64_response_content():
    raw = b"\x89PNG\r\n\x1a\n"
    har = {
        "log": {
            "version": "1.2",
            "entries": [
                {
                    "request": {"method": "GET", "url": "http://example.test/logo.png", "headers": []},
                    "response": {
                        "status": 200,
                        "statusText": "OK",
                        "headers": [],
                        "content": {
                            "mimeType": "image/png",
                            "text": base64.b64encode(raw).decode(),
                            "encoding": "base64",
                        },
                    },
                }
            ],
        }
    }
    fixture = load_fixture(json.dumps(har), "har")
    assert fixture.exchanges[0].response.body.to_bytes() == raw
    assert fixture.exchanges[0].response.body.base64 is not None


def test_dump_har_produces_valid_minimal_archive():
    fixture = Fixture(
        exchanges=[
            Exchange(
                request=Request(
                    method="POST",
                    url="http://example.test/submit?a=1&b=two",
                    headers=[("Content-Type", "text/plain")],
                    body=Body(text="hello", mime="text/plain"),
                ),
                response=Response(
                    status=204,
                    status_text="No Content",
                    headers=[("X-Done", "yes")],
                ),
            )
        ]
    )
    har = json.loads(dump_fixture(fixture, "har"))
    entry = har["log"]["entries"][0]
    assert har["log"]["version"] == "1.2"
    assert har["log"]["creator"]["name"] == "fixmux"
    assert entry["request"]["queryString"] == [
        {"name": "a", "value": "1"},
        {"name": "b", "value": "two"},
    ]
    assert entry["request"]["postData"]["text"] == "hello"
    assert entry["response"]["status"] == 204
    # Required-by-spec fields carry deterministic sentinels.
    assert entry["startedDateTime"] == "1970-01-01T00:00:00+00:00"
    assert entry["response"]["content"]["size"] == 0


def test_har_roundtrip_preserves_binary_bodies_on_both_sides():
    raw = bytes(range(256))
    fixture = Fixture(
        exchanges=[
            Exchange(
                request=Request(method="PUT", url="http://example.test/u", body=Body.from_bytes(raw)),
                response=Response(body=Body.from_bytes(raw, mime="application/octet-stream")),
            )
        ]
    )
    text = dump_fixture(fixture, "har")
    content = json.loads(text)["log"]["entries"][0]["response"]["content"]
    assert content["encoding"] == "base64"
    assert base64.b64decode(content["text"]) == raw
    reloaded = load_fixture(text, "har")
    assert reloaded.exchanges[0].request.body.to_bytes() == raw
    assert reloaded.exchanges[0].response.body.to_bytes() == raw


def test_har_load_dump_load_is_stable(har_text):
    once = load_fixture(har_text, "har")
    text = dump_fixture(once, "har")
    twice = load_fixture(text, "har")
    assert dump_fixture(twice, "har") == text
