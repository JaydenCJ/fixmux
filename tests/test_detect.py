"""Tests for structural format detection.

Every input here is JSON or YAML text with no file extension to lean on —
detection must work from shape alone, and refuse rather than guess."""

from __future__ import annotations

import pytest

from fixmux import detect_format
from fixmux.errors import DetectError


def test_detects_har_and_nock_json_shapes(har_text, nock_text):
    assert detect_format(har_text) == "har"
    assert detect_format(nock_text) == "nock"


def test_detects_both_vcr_yaml_dialects(vcrpy_yaml_text, ruby_vcr_text):
    assert detect_format(vcrpy_yaml_text) == "vcr"
    assert detect_format(ruby_vcr_text) == "vcr-ruby"


def test_detects_vcr_json_dialects():
    assert detect_format('{"interactions": [], "version": 1}') == "vcr"
    assert detect_format('{"http_interactions": [], "recorded_with": "VCR"}') == "vcr-ruby"


def test_detects_wiremock_both_shapes(wiremock_text):
    assert detect_format(wiremock_text) == "wiremock"
    single = '{"request": {"method": "GET", "url": "/x"}, "response": {"status": 200}}'
    assert detect_format(single) == "wiremock"


def test_unknown_input_raises_detect_error():
    with pytest.raises(DetectError):
        detect_format('{"some": "other json"}')
    with pytest.raises(DetectError):
        detect_format("just some text\n")
