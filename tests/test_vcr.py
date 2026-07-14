"""Tests for the VCR cassette codec — the vcrpy and Ruby VCR dialects, in
both their YAML and JSON serializations."""

from __future__ import annotations

import base64
import json

import pytest

from fixmux import convert, dump_fixture, load_fixture, yamlite
from fixmux.errors import ParseError, UnsupportedFeatureError
from fixmux.model import Body, Exchange, Fixture, Request, Response


def test_load_vcrpy_yaml_cassette(vcrpy_yaml_text):
    fixture = load_fixture(vcrpy_yaml_text, "vcr")
    assert len(fixture.exchanges) == 2
    get, post = fixture.exchanges
    assert get.request.method == "GET"
    assert get.request.url == "http://example.test/api/members?page=2"
    assert ("X-Trace", "one") in get.request.headers
    assert ("X-Trace", "two") in get.request.headers
    assert post.request.body.text == '{"name": "chika"}'
    assert post.response.status == 201
    assert post.response.status_text == "Created"
    # Body mime is inferred from the Content-Type header.
    assert get.response.body.mime == "application/json"


def test_load_ruby_cassette_normalizes_method_and_timestamp(ruby_vcr_text):
    fixture = load_fixture(ruby_vcr_text, "vcr-ruby")
    assert fixture.meta["recorded_with"] == "VCR 6.2.0"
    first = fixture.exchanges[0]
    assert first.request.method == "GET"  # ruby records lowercase
    assert first.meta["recorded_at"] == "2026-03-01T09:30:00+00:00"


def test_load_vcrpy_json_serializer_cassette():
    cassette = {
        "interactions": [
            {
                "request": {
                    "body": None,
                    "headers": {"Accept": ["*/*"]},
                    "method": "GET",
                    "uri": "http://example.test/ping",
                },
                "response": {
                    "body": {"string": "pong"},
                    "headers": {},
                    "status": {"code": 200, "message": "OK"},
                },
            }
        ],
        "version": 1,
    }
    fixture = load_fixture(json.dumps(cassette), "vcr")
    assert fixture.exchanges[0].response.body.text == "pong"


def test_dump_vcrpy_yaml_matches_vcrpy_shape(vcrpy_yaml_text):
    fixture = load_fixture(vcrpy_yaml_text, "vcr")
    out = dump_fixture(fixture, "vcr")
    data = yamlite.loads(out)
    assert data["version"] == 1
    interaction = data["interactions"][0]
    assert interaction["request"]["body"] is None  # empty request body is null
    assert interaction["response"]["body"] == {"string": '{"members": ["aya", "ben"]}'}
    assert interaction["request"]["headers"]["X-Trace"] == ["one", "two"]


def test_vcrpy_yaml_roundtrip_is_byte_stable(vcrpy_yaml_text):
    fixture = load_fixture(vcrpy_yaml_text, "vcr")
    out = dump_fixture(fixture, "vcr")
    assert dump_fixture(load_fixture(out, "vcr"), "vcr") == out


def test_dump_ruby_dialect_writes_document_marker_and_rfc2822(ruby_vcr_text):
    fixture = load_fixture(ruby_vcr_text, "vcr-ruby")
    out = dump_fixture(fixture, "vcr-ruby")
    assert out.startswith("---\n")
    assert "recorded_at: Sun, 01 Mar 2026 09:30:00 GMT" in out
    assert "method: get" in out
    assert "recorded_with: VCR 6.2.0" in out


def test_binary_body_dumps_as_yaml_binary_tag_and_reloads():
    raw = b"\x00\x01\xfe\xffgif89a"
    fixture = Fixture(
        exchanges=[
            Exchange(
                request=Request(url="http://example.test/img"),
                response=Response(body=Body.from_bytes(raw)),
            )
        ]
    )
    out = dump_fixture(fixture, "vcr")
    assert "!!binary" in out
    reloaded = load_fixture(out, "vcr")
    assert reloaded.exchanges[0].response.body.to_bytes() == raw


def test_ruby_base64_string_body_loads_and_roundtrips():
    raw = b"\x89PNG\r\n"
    cassette = (
        "---\nhttp_interactions:\n- request:\n    method: get\n"
        "    uri: http://example.test/logo\n    body:\n      encoding: UTF-8\n"
        "      string: ''\n    headers: {}\n  response:\n    status:\n      code: 200\n"
        "      message: OK\n    headers: {}\n    body:\n      encoding: ASCII-8BIT\n"
        "      base64_string: %s\n  recorded_at: Sun, 01 Mar 2026 00:00:00 GMT\n"
        "recorded_with: VCR 6.2.0\n" % base64.b64encode(raw).decode()
    )
    fixture = load_fixture(cassette, "vcr-ruby")
    assert fixture.exchanges[0].response.body.to_bytes() == raw
    out = dump_fixture(fixture, "vcr-ruby")
    assert "base64_string" in out and "ASCII-8BIT" in out


def test_json_serializer_works_but_refuses_binary_in_strict_mode():
    text_fixture = Fixture(
        exchanges=[
            Exchange(
                request=Request(url="http://example.test/"),
                response=Response(status=200, body=Body(text="hi")),
            )
        ]
    )
    out = dump_fixture(text_fixture, "vcr", serializer="json")
    assert json.loads(out)["interactions"][0]["response"]["body"] == {"string": "hi"}
    binary_fixture = Fixture(
        exchanges=[
            Exchange(
                request=Request(url="http://example.test/"),
                response=Response(body=Body.from_bytes(b"\x00\xff")),
            )
        ]
    )
    with pytest.raises(UnsupportedFeatureError):
        dump_fixture(binary_fixture, "vcr", serializer="json", strict=True)
    notes = []
    out = dump_fixture(binary_fixture, "vcr", serializer="json", notes=notes)
    assert notes and "binary" in notes[0]
    assert "base64_string" in out


def test_missing_uri_is_a_parse_error():
    with pytest.raises(ParseError, match="uri"):
        load_fixture("interactions:\n- request:\n    method: GET\n", "vcr")


def test_convert_between_vcr_dialects_swaps_root_key(vcrpy_yaml_text):
    result = convert(vcrpy_yaml_text, to_format="vcr-ruby")
    assert result.from_format == "vcr"
    assert "http_interactions:" in result.text
    back = convert(result.text, to_format="vcr")
    assert back.text.startswith("interactions:")
