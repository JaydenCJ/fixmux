"""Cross-format conversion tests: the whole point of fixmux.

The core test converts every sample capture (HAR, vcrpy, Ruby VCR, nock)
into every target dialect and back, then asserts semantic equivalence
through the comparison layer — method, URL, headers, bodies, and status
must all survive the round trip, for all 20 source/target pairs."""

from __future__ import annotations

import pytest

from fixmux import compare_fixtures, convert, load_fixture

SOURCES = [
    ("har_text", "har"),
    ("vcrpy_yaml_text", "vcr"),
    ("ruby_vcr_text", "vcr-ruby"),
    ("nock_text", "nock"),
]


@pytest.mark.parametrize("target", ["har", "vcr", "vcr-ruby", "nock", "wiremock"])
def test_every_source_roundtrips_through_target(target, request):
    for fixture_name, source_format in SOURCES:
        source_text = request.getfixturevalue(fixture_name)
        there = convert(source_text, to_format=target)
        assert there.from_format == source_format
        assert there.exchanges == 2
        # WireMock drops the origin; supply it again on the way back.
        base_url = "http://example.test" if target == "wiremock" else None
        back = convert(there.text, to_format=source_format, base_url=base_url)
        original = load_fixture(source_text, source_format)
        returned = load_fixture(back.text, source_format)
        result = compare_fixtures(original, returned)
        assert result.equivalent, "%s -> %s -> %s:\n%s" % (
            source_format,
            target,
            source_format,
            "\n".join(d.render() for d in result.differences),
        )


def test_all_four_sample_dialects_describe_the_same_exchanges(
    har_text, vcrpy_yaml_text, ruby_vcr_text, nock_text
):
    # The conftest samples were written independently per dialect; fixmux
    # must see through the surface differences (duplicate-header encodings,
    # parsed JSON bodies, lowercase methods, explicit default ports).
    fixtures = [
        load_fixture(har_text, "har"),
        load_fixture(vcrpy_yaml_text, "vcr"),
        load_fixture(ruby_vcr_text, "vcr-ruby"),
        load_fixture(nock_text, "nock"),
    ]
    reference = fixtures[0]
    for other in fixtures[1:]:
        result = compare_fixtures(reference, other)
        assert result.equivalent, "\n".join(d.render() for d in result.differences)


def test_wiremock_conversion_notes_the_dropped_host(vcrpy_yaml_text):
    there = convert(vcrpy_yaml_text, to_format="wiremock")
    assert any("host" in note for note in there.notes)


def test_convert_reports_metadata_and_is_deterministic(har_text):
    first = convert(har_text, to_format="nock")
    assert first.from_format == "har"
    assert first.to_format == "nock"
    assert first.exchanges == 2
    assert first.text == convert(har_text, to_format="nock").text


def test_recorded_at_survives_har_to_ruby_and_back(har_text):
    ruby = convert(har_text, to_format="vcr-ruby").text
    assert "recorded_at: Sun, 01 Mar 2026 09:30:00 GMT" in ruby
    har_again = convert(ruby, to_format="har").text
    assert "2026-03-01T09:30:00+00:00" in har_again


def test_comparison_detects_real_drift(har_text, vcrpy_yaml_text):
    original = load_fixture(har_text, "har")
    other = load_fixture(vcrpy_yaml_text, "vcr")
    other.exchanges[1].response.status = 500
    result = compare_fixtures(original, other)
    assert not result.equivalent
    assert any("status" in d.field for d in result.differences)


def test_comparison_detects_missing_exchange(har_text):
    left = load_fixture(har_text, "har")
    right = load_fixture(har_text, "har")
    right.exchanges.pop()
    result = compare_fixtures(left, right)
    assert not result.equivalent
    assert result.differences[0].field == "exchange count"
