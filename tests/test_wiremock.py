"""Tests for the WireMock stub-mapping codec: both file shapes, equality
matchers, the hostless-stub rules, and lenient/strict degradation."""

from __future__ import annotations

import json

import pytest

from fixmux import dump_fixture, load_fixture
from fixmux.errors import UnsupportedFeatureError
from fixmux.model import Body, Exchange, Fixture, Request, Response


def test_load_both_file_shapes(wiremock_text):
    fixture = load_fixture(wiremock_text, "wiremock")
    assert len(fixture.exchanges) == 2
    post = fixture.exchanges[1]
    assert post.request.method == "POST"
    assert post.request.body.text == '{"name": "chika"}'
    assert post.response.status_text == "Created"
    # A single mapping file (no "mappings" wrapper) loads the same way.
    single = {
        "request": {"method": "GET", "url": "/health"},
        "response": {"status": 200, "body": "up"},
    }
    fixture = load_fixture(json.dumps(single), "wiremock")
    assert len(fixture.exchanges) == 1
    assert fixture.exchanges[0].response.body.text == "up"


def test_base_url_defaults_to_wiremock_loopback_and_can_be_overridden(wiremock_text):
    fixture = load_fixture(wiremock_text, "wiremock")
    assert fixture.exchanges[0].request.url == "http://127.0.0.1:8080/api/members?page=2"
    overridden = load_fixture(wiremock_text, "wiremock", base_url="https://api.example.test")
    assert overridden.exchanges[0].request.url.startswith("https://api.example.test/")


def test_url_path_plus_query_parameters_reassemble_the_url():
    mapping = {
        "request": {
            "method": "GET",
            "urlPath": "/api/search",
            "queryParameters": {"q": {"equalTo": "hello world"}, "page": {"equalTo": "2"}},
        },
        "response": {"status": 200},
    }
    fixture = load_fixture(json.dumps(mapping), "wiremock")
    url = fixture.exchanges[0].request.url
    assert "/api/search?" in url and "q=hello+world" in url and "page=2" in url


def test_json_body_is_serialized_deterministically():
    mapping = {
        "request": {"method": "GET", "url": "/j"},
        "response": {"status": 200, "jsonBody": {"b": 2, "a": 1}},
    }
    fixture = load_fixture(json.dumps(mapping), "wiremock")
    assert fixture.exchanges[0].response.body.text == '{"a": 1, "b": 2}'
    assert fixture.exchanges[0].response.body.mime == "application/json"


def test_regex_url_matcher_degrades_with_a_note():
    mapping = {
        "request": {"method": "GET", "urlPattern": "/api/members/[0-9]+"},
        "response": {"status": 200},
    }
    notes = []
    fixture = load_fixture(json.dumps(mapping), "wiremock", notes=notes)
    assert notes and "regex" in notes[0]
    assert fixture.exchanges[0].request.url.endswith("/api/members/[0-9]+")


def test_non_equality_header_matcher_is_dropped_with_note():
    mapping = {
        "request": {
            "method": "GET",
            "url": "/x",
            "headers": {"Authorization": {"matches": "Bearer .*"}},
        },
        "response": {"status": 200},
    }
    notes = []
    fixture = load_fixture(json.dumps(mapping), "wiremock", notes=notes)
    assert fixture.exchanges[0].request.headers == []
    assert any("non-equality" in note for note in notes)


def test_dump_drops_host_with_note_and_strict_raises():
    fixture = Fixture(
        exchanges=[
            Exchange(
                request=Request(method="GET", url="http://example.test/api"),
                response=Response(status=200, body=Body(text="ok")),
            )
        ]
    )
    notes = []
    out = dump_fixture(fixture, "wiremock", notes=notes)
    mapping = json.loads(out)
    assert mapping["request"]["url"] == "/api"
    assert any("example.test" in note for note in notes)
    with pytest.raises(UnsupportedFeatureError):
        dump_fixture(fixture, "wiremock", strict=True)


def test_dump_multiple_exchanges_uses_mappings_export_shape(wiremock_text):
    fixture = load_fixture(wiremock_text, "wiremock")
    out = json.loads(dump_fixture(fixture, "wiremock"))
    assert len(out["mappings"]) == 2
    assert out["mappings"][0]["request"]["url"] == "/api/members?page=2"


def test_binary_bodies_roundtrip_via_base64_fields():
    raw = b"\x00\x01\x02\x03"
    fixture = Fixture(
        exchanges=[
            Exchange(
                request=Request(method="PUT", url="http://example.test/up", body=Body.from_bytes(raw)),
                response=Response(status=200, body=Body.from_bytes(raw)),
            )
        ]
    )
    out = dump_fixture(fixture, "wiremock")
    reloaded = load_fixture(out, "wiremock")
    assert reloaded.exchanges[0].request.body.to_bytes() == raw
    assert reloaded.exchanges[0].response.body.to_bytes() == raw
