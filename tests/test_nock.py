"""Tests for the nock definition codec: recorder output in, nock.define
input out, including rawHeaders ordering and the binary hex convention."""

from __future__ import annotations

import json

import pytest

from fixmux import dump_fixture, load_fixture
from fixmux.errors import ParseError, UnsupportedFeatureError
from fixmux.model import Body, Exchange, Fixture, Request, Response


def test_load_definitions_array(nock_text):
    fixture = load_fixture(nock_text, "nock")
    assert len(fixture.exchanges) == 2
    get = fixture.exchanges[0]
    assert get.request.url == "http://example.test:80/api/members?page=2"
    assert get.response.status == 200


def test_parsed_json_response_is_serialized_with_sorted_keys(nock_text):
    fixture = load_fixture(nock_text, "nock")
    assert fixture.exchanges[0].response.body.text == '{"members": ["aya", "ben"]}'
    assert fixture.exchanges[0].response.body.mime == "application/json"


def test_raw_headers_preserve_order_and_duplicates():
    definition = [
        {
            "scope": "http://example.test:80",
            "method": "GET",
            "path": "/dup",
            "body": "",
            "status": 200,
            "response": "ok",
            "rawHeaders": ["Set-Cookie", "a=1", "Set-Cookie", "b=2", "X-Last", "z"],
        }
    ]
    fixture = load_fixture(json.dumps(definition), "nock")
    assert fixture.exchanges[0].response.headers == [
        ("Set-Cookie", "a=1"),
        ("Set-Cookie", "b=2"),
        ("X-Last", "z"),
    ]
    # An odd-length rawHeaders array is corrupt input, not something to guess at.
    definition[0]["rawHeaders"] = ["only-a-name"]
    with pytest.raises(ParseError, match="rawHeaders"):
        load_fixture(json.dumps(definition), "nock")


def test_json_request_body_object_is_normalized():
    definition = [
        {"scope": "http://example.test", "path": "/create", "method": "POST",
         "status": 201, "response": "", "body": {"z": 1, "a": 2}}
    ]
    fixture = load_fixture(json.dumps(definition), "nock")
    assert fixture.exchanges[0].request.body.text == '{"a": 2, "z": 1}'


def test_response_is_binary_hex_decodes():
    raw = b"\x89PNG\r\n\x1a\n"
    definition = [
        {"scope": "http://example.test", "path": "/logo.png", "method": "GET",
         "status": 200, "response": raw.hex(), "responseIsBinary": True}
    ]
    fixture = load_fixture(json.dumps(definition), "nock")
    assert fixture.exchanges[0].response.body.to_bytes() == raw


def test_dump_emits_scope_with_explicit_port_and_reqheaders():
    fixture = Fixture(
        exchanges=[
            Exchange(
                request=Request(
                    method="GET",
                    url="https://api.example.test/v1/items?limit=5",
                    headers=[("Accept", "application/json")],
                ),
                response=Response(
                    status=200,
                    headers=[("Content-Type", "application/json")],
                    body=Body(text='{"items": []}', mime="application/json"),
                ),
            )
        ]
    )
    out = json.loads(dump_fixture(fixture, "nock"))
    assert out[0]["scope"] == "https://api.example.test:443"
    assert out[0]["path"] == "/v1/items?limit=5"
    assert out[0]["response"] == {"items": []}  # JSON bodies stored parsed
    assert out[0]["reqheaders"] == {"Accept": "application/json"}


def test_dump_binary_response_uses_hex_and_flag():
    raw = b"\x00\x01\x02\xff"
    fixture = Fixture(
        exchanges=[
            Exchange(
                request=Request(url="http://example.test/bin"),
                response=Response(body=Body.from_bytes(raw)),
            )
        ]
    )
    out = json.loads(dump_fixture(fixture, "nock"))
    assert out[0]["responseIsBinary"] is True
    assert bytes.fromhex(out[0]["response"]) == raw


def test_dump_binary_request_body_raises_in_strict_mode():
    fixture = Fixture(
        exchanges=[
            Exchange(
                request=Request(method="PUT", url="http://example.test/up",
                                body=Body.from_bytes(b"\x00\xff")),
                response=Response(status=200),
            )
        ]
    )
    with pytest.raises(UnsupportedFeatureError):
        dump_fixture(fixture, "nock", strict=True)
    notes = []
    dump_fixture(fixture, "nock", notes=notes)
    assert notes and "binary request body" in notes[0]


def test_nock_load_dump_load_is_stable(nock_text):
    once = load_fixture(nock_text, "nock")
    text = dump_fixture(once, "nock")
    assert dump_fixture(load_fixture(text, "nock"), "nock") == text
