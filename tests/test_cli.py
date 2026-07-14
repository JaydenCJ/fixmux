"""End-to-end tests for the ``fixmux`` command-line interface.

Every test drives :func:`fixmux.cli.main` in-process with real files under
``tmp_path`` — no subprocesses, no network — and asserts on stdout, stderr,
and exit codes exactly as a shell pipeline would see them.
"""

from __future__ import annotations

import json

import pytest

from fixmux import __version__
from fixmux.cli import main


@pytest.fixture
def har_file(tmp_path, har_text):
    path = tmp_path / "capture.har"
    path.write_text(har_text, encoding="utf-8")
    return path


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    assert capsys.readouterr().out.strip() == "fixmux %s" % __version__


def test_convert_writes_target_format_to_stdout(har_file, capsys):
    assert main(["convert", str(har_file), "-t", "nock"]) == 0
    definitions = json.loads(capsys.readouterr().out)
    assert definitions[0]["scope"] == "http://example.test:80"
    assert definitions[0]["path"] == "/api/members?page=2"


def test_convert_writes_output_file_and_reports_on_stderr(har_file, tmp_path, capsys):
    out_file = tmp_path / "cassette.yml"
    assert main(["convert", str(har_file), "-t", "vcr", "-o", str(out_file)]) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "har -> vcr: wrote 2 exchanges" in captured.err
    assert out_file.read_text(encoding="utf-8").startswith("interactions:")


def test_convert_reads_stdin(har_text, capsys, monkeypatch):
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO(har_text))
    assert main(["convert", "-", "-t", "vcr-ruby"]) == 0
    assert capsys.readouterr().out.startswith("---\nhttp_interactions:")


def test_convert_emits_notes_on_stderr_but_strict_fails(har_file, capsys):
    assert main(["convert", str(har_file), "-t", "wiremock"]) == 0
    captured = capsys.readouterr()
    assert "note:" in captured.err and "host" in captured.err
    assert main(["convert", str(har_file), "-t", "wiremock", "--strict"]) == 2
    assert "error:" in capsys.readouterr().err


def test_convert_bad_inputs_exit_2(har_file, tmp_path, capsys):
    assert main(["convert", str(har_file), "-t", "postman"]) == 2
    assert "unknown format" in capsys.readouterr().err
    bogus = tmp_path / "notes.json"
    bogus.write_text('{"hello": "world"}', encoding="utf-8")
    assert main(["convert", str(bogus), "-t", "har"]) == 2
    assert "could not detect" in capsys.readouterr().err


def test_convert_reports_a_malformed_port_instead_of_crashing(tmp_path, capsys):
    # Dumping to nock splits the URL into scope + path; a hand-edited
    # cassette with a non-numeric port must exit 2 with a clear message.
    cassette = tmp_path / "cassette.yml"
    cassette.write_text(
        "interactions:\n"
        "- request:\n"
        "    method: GET\n"
        "    uri: http://example.test:port/a\n"
        "  response:\n"
        "    status:\n"
        "      code: 200\n"
        "version: 1\n",
        encoding="utf-8",
    )
    assert main(["convert", str(cassette), "-t", "nock"]) == 2
    assert "invalid port" in capsys.readouterr().err


def test_detect_prints_format_and_exchange_count(har_file, tmp_path, vcrpy_yaml_text, capsys):
    cassette = tmp_path / "cassette.yml"
    cassette.write_text(vcrpy_yaml_text, encoding="utf-8")
    assert main(["detect", str(har_file), str(cassette)]) == 0
    out = capsys.readouterr().out
    assert "har\t2 exchanges" in out
    assert "vcr\t2 exchanges" in out


def test_inspect_summarizes_exchanges(har_file, capsys):
    assert main(["inspect", str(har_file)]) == 0
    out = capsys.readouterr().out
    assert "format: har" in out
    assert "exchanges: 2" in out
    assert "GET http://example.test/api/members?page=2 -> 200" in out
    assert "POST http://example.test/api/members -> 201" in out


def test_verify_equivalent_after_conversion(har_file, tmp_path, capsys):
    nock_file = tmp_path / "fixtures.json"
    assert main(["convert", str(har_file), "-t", "nock", "-o", str(nock_file)]) == 0
    capsys.readouterr()
    assert main(["verify", str(har_file), str(nock_file)]) == 0
    assert "equivalent: 2 exchanges" in capsys.readouterr().out
    # A pair involving WireMock compares path+query only, automatically.
    stub_file = tmp_path / "stubs.json"
    assert main(["convert", str(har_file), "-t", "wiremock", "-o", str(stub_file)]) == 0
    capsys.readouterr()
    assert main(["verify", str(har_file), str(stub_file)]) == 0
    assert "equivalent" in capsys.readouterr().out


def test_verify_detects_drift_with_exit_code_1(har_file, tmp_path, har_text, capsys):
    drifted = json.loads(har_text)
    drifted["log"]["entries"][0]["response"]["status"] = 503
    other = tmp_path / "drifted.har"
    other.write_text(json.dumps(drifted), encoding="utf-8")
    assert main(["verify", str(har_file), str(other)]) == 1
    out = capsys.readouterr().out
    assert "response status differs" in out
    assert "not equivalent" in out


def test_formats_lists_all_five_dialects(capsys):
    assert main(["formats"]) == 0
    out = capsys.readouterr().out
    for format_id in ("har", "vcr", "vcr-ruby", "wiremock", "nock"):
        assert format_id in out


def test_missing_file_exits_2(capsys):
    assert main(["inspect", "/no/such/fixture.har"]) == 2
    assert "error" in capsys.readouterr().err
