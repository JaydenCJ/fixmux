"""Tests for the intermediate exchange model and its URL/header helpers."""

from __future__ import annotations

import base64

import pytest

from fixmux import model
from fixmux.errors import ParseError
from fixmux.model import Body


def test_normalize_url_strips_default_port_and_adds_root_path():
    assert model.split_url("https://Example.TEST:8443/a/b?x=1") == (
        "https", "example.test", 8443, "/a/b", "x=1",
    )
    assert model.normalize_url("http://example.test:80") == "http://example.test/"
    assert model.normalize_url("https://example.test:443/x") == "https://example.test/x"
    # A non-default port is significant and must survive.
    assert model.normalize_url("http://example.test:8080/") == "http://example.test:8080/"


def test_split_url_rejects_a_non_numeric_port_with_a_fixmux_error():
    # A hand-edited fixture with a bad authority must raise ParseError so the
    # CLI reports it (exit 2) instead of dying with a ValueError traceback.
    with pytest.raises(ParseError, match="invalid port"):
        model.split_url("http://example.test:port/a")


def test_origin_and_path_helpers():
    url = "http://example.test/api"
    assert model.origin(url) == "http://example.test"
    assert model.origin(url, explicit_port=True) == "http://example.test:80"
    assert model.path_and_query("http://example.test") == "/"
    assert model.path_and_query("http://example.test/a?b=1") == "/a?b=1"


def test_body_from_bytes_prefers_utf8_text():
    assert Body.from_bytes(b'{"ok": true}').text == '{"ok": true}'


def test_body_from_bytes_keeps_binary_as_base64():
    raw = b"\x89PNG\r\n\x1a\n\x00\x00"
    body = Body.from_bytes(raw)
    assert body.text is None
    assert base64.b64decode(body.base64) == raw
    assert body.to_bytes() == raw


def test_first_header_is_case_insensitive():
    headers = [("Content-Type", "application/json"), ("X-A", "1")]
    assert model.first_header(headers, "content-type") == "application/json"
    assert model.first_header(headers, "missing") is None


def test_header_multimap_groups_duplicates_preserving_first_casing():
    headers = [("X-Trace", "one"), ("x-trace", "two"), ("Accept", "*/*")]
    assert model.header_multimap(headers) == {
        "X-Trace": ["one", "two"],
        "Accept": ["*/*"],
    }


def test_timestamps_parse_and_roundtrip_between_iso_and_rfc2822():
    assert model.parse_when("2026-03-01T09:30:00Z") == "2026-03-01T09:30:00+00:00"
    assert model.parse_when("not a date") is None
    meta = {"recorded_at": model.parse_when("Sun, 01 Mar 2026 09:30:00 GMT")}
    assert meta["recorded_at"] == "2026-03-01T09:30:00+00:00"
    assert model.to_rfc2822(meta) == "Sun, 01 Mar 2026 09:30:00 GMT"
    assert model.to_iso({}) == model.EPOCH_ISO
