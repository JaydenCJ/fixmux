"""Tests for the dependency-free YAML subset engine.

yamlite only has to handle the YAML that psych (Ruby) and PyYAML (Python)
emit for VCR cassettes — but it has to handle *all* of that, including the
folded multi-line scalars PyYAML produces when it wraps output at column
80. These tests pin down every construct the two serializers use, plus the
explicit rejections (anchors, unknown tags, multiple documents, tabs).
"""

from __future__ import annotations

import base64

import pytest

from fixmux import yamlite
from fixmux.errors import ParseError


# ---------------------------------------------------------------------------
# Scalars
# ---------------------------------------------------------------------------


def test_plain_scalar_type_resolution():
    doc = "a: null\nb: ~\nc: true\nd: false\ne: 42\nf: -7\ng: 3.5\nh: hello\ni:\n"
    assert yamlite.loads(doc) == {
        "a": None, "b": None, "c": True, "d": False, "e": 42, "f": -7,
        "g": 3.5, "h": "hello", "i": None,
    }


def test_quoted_scalar_escapes():
    doc = "s: 'it''s here'\nd: \"line1\\nline2\\ttab \\\"q\\\" \\u00e9\"\n"
    assert yamlite.loads(doc) == {
        "s": "it's here",
        "d": 'line1\nline2\ttab "q" \xe9',
    }


def test_plain_scalar_keeps_urls_and_timestamps_as_strings():
    doc = "uri: http://example.test:8080/path?a=1\nwhen: Tue, 01 Nov 2011 04:58:44 GMT\n"
    data = yamlite.loads(doc)
    assert data["uri"] == "http://example.test:8080/path?a=1"
    assert data["when"] == "Tue, 01 Nov 2011 04:58:44 GMT"


def test_multiline_quoted_scalar_folding():
    # PyYAML wraps long quoted scalars across lines: a break folds to one
    # space, a blank line folds to a newline.
    doc = "body: '{\"note\": \"a very long\n    wrapped value\"}'\nblank: 'first\n\n  second'\n"
    assert yamlite.loads(doc) == {
        "body": '{"note": "a very long wrapped value"}',
        "blank": "first\nsecond",
    }


def test_multiline_plain_scalar_folds_to_spaces():
    doc = "key: alpha beta\n  gamma delta\nnext: 1\n"
    assert yamlite.loads(doc) == {"key": "alpha beta gamma delta", "next": 1}


def test_plain_scalar_trailing_comment_is_stripped():
    assert yamlite.loads("key: value # a comment\n") == {"key": "value"}


def test_block_literal_chomping_variants():
    strip = yamlite.loads("b: |-\n  line1\n  line2\n\n  line4\nnext: 2\n")
    assert strip == {"b": "line1\nline2\n\nline4", "next": 2}
    clip = yamlite.loads("b: |\n  x\n  y\n")
    assert clip == {"b": "x\ny\n"}


def test_block_folded_joins_lines():
    assert yamlite.loads("b: >-\n  one\n  two\n\n  three\n") == {"b": "one two\nthree"}


def test_binary_tag_decodes_to_bytes():
    payload = b"\x00\x01\xffbinary body"
    encoded = base64.b64encode(payload).decode()
    doc = "body:\n  string: !!binary |\n    %s\n" % encoded
    assert yamlite.loads(doc) == {"body": {"string": payload}}


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------


def test_nested_mappings_and_sequences():
    doc = (
        "interactions:\n"
        "- request:\n"
        "    headers:\n"
        "      Accept:\n"
        "      - '*/*'\n"
        "    method: GET\n"
        "  response:\n"
        "    status:\n"
        "      code: 200\n"
        "version: 1\n"
    )
    assert yamlite.loads(doc) == {
        "interactions": [
            {
                "request": {"headers": {"Accept": ["*/*"]}, "method": "GET"},
                "response": {"status": {"code": 200}},
            }
        ],
        "version": 1,
    }


def test_sequence_indented_deeper_than_parent_key():
    # psych sometimes indents dashes; both placements must parse identically.
    assert yamlite.loads("k:\n  - a\n  - b\n") == yamlite.loads("k:\n- a\n- b\n")


def test_flow_collections_inline():
    doc = "empty_map: {}\nempty_list: []\ninline: {a: 1, b: [x, y]}\n"
    assert yamlite.loads(doc) == {
        "empty_map": {},
        "empty_list": [],
        "inline": {"a": 1, "b": ["x", "y"]},
    }


def test_document_start_marker_and_comments():
    doc = "---\n# a cassette\nkey: value\n"
    assert yamlite.loads(doc) == {"key": "value"}


def test_quoted_mapping_keys():
    assert yamlite.loads("'Content-Type': text/plain\n") == {"Content-Type": "text/plain"}


def test_unsupported_constructs_are_rejected_loudly():
    with pytest.raises(ParseError, match="anchors"):
        yamlite.loads("a: &anchor 1\nb: *anchor\n")
    with pytest.raises(ParseError, match="tag"):
        yamlite.loads("a: !ruby/object:Foo {}\n")
    with pytest.raises(ParseError, match="multi-document"):
        yamlite.loads("---\na: 1\n---\nb: 2\n")
    with pytest.raises(ParseError, match="tabs"):
        yamlite.loads("a:\n\tb: 1\n")


# ---------------------------------------------------------------------------
# Emitting and round trips
# ---------------------------------------------------------------------------


def test_dump_quotes_strings_that_would_change_type():
    out = yamlite.dumps({"a": "true", "b": "123", "c": "null", "d": "3.5"})
    assert yamlite.loads(out) == {"a": "true", "b": "123", "c": "null", "d": "3.5"}
    assert "'true'" in out and "'123'" in out


def test_dump_multiline_strings_and_bytes_use_block_forms():
    out = yamlite.dumps({"body": "line1\nline2"})
    assert "|-" in out
    assert yamlite.loads(out) == {"body": "line1\nline2"}
    payload = bytes(range(20))
    out = yamlite.dumps({"body": payload})
    assert "!!binary" in out
    assert yamlite.loads(out) == {"body": payload}


def test_dump_load_roundtrip_of_nested_cassette_shape():
    data = {
        "interactions": [
            {
                "request": {
                    "body": None,
                    "headers": {"Accept": ["*/*"], "X-N": ["1", "2"]},
                    "method": "GET",
                    "uri": "http://example.test/api?q=hello%20world",
                },
                "response": {
                    "body": {"string": '{"ok": true, "note": "it\'s fine"}'},
                    "status": {"code": 200, "message": "OK"},
                },
            }
        ],
        "version": 1,
    }
    out = yamlite.dumps(data)
    assert yamlite.loads(out) == data
    # Keys come out sorted, matching what PyYAML writes for cassettes.
    assert out.index("body:") < out.index("headers:") < out.index("method:")


def test_dump_roundtrip_special_strings():
    tricky = {
        "colon": "key: value",
        "hash": "a # b",
        "empty": "",
        "unicode": "こんにちは世界",
        "spaces": "  padded  ",
        "newline_end": "text\n",
        "control": "bell\x07",
    }
    assert yamlite.loads(yamlite.dumps(tricky)) == tricky
