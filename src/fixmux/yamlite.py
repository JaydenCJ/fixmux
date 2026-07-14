"""A dependency-free YAML subset reader/writer for VCR cassettes.

fixmux ships no runtime dependencies, yet both Ruby VCR and Python vcrpy
serialize cassettes as YAML by default. This module implements exactly the
YAML subset those two ecosystems (psych and PyYAML) emit for cassettes:

* block mappings and block sequences, including compact ``- key: value``
  sequence entries and sequences indented at their parent key's column;
* plain, single-quoted, and double-quoted scalars — including the multi-line
  folded forms PyYAML produces when it wraps long lines at width 80;
* block scalars (``|``, ``>``) with chomping and indentation indicators;
* flow collections (``{}``, ``[]``) for empty and inline maps/lists;
* the ``!!binary`` tag (base64 payloads become :class:`bytes`);
* comments and the ``---`` document start marker.

Anchors, aliases, custom tags, and multi-document streams are rejected with
a clear :class:`~fixmux.errors.ParseError` — cassette serializers never emit
them, and silently guessing would risk corrupting a fixture.

The emitter mirrors PyYAML's block style (sorted keys, two-space indent,
sequence dashes at the parent key's column) so converted cassettes diff
cleanly against natively recorded ones. It never folds long lines, which
keeps output deterministic and trivially greppable.
"""

from __future__ import annotations

import base64
import json
import re
from typing import Any, List, Optional, Tuple

from .errors import ParseError

__all__ = ["loads", "dumps"]


# ---------------------------------------------------------------------------
# Scalar resolution (YAML core schema, the part cassettes use)
# ---------------------------------------------------------------------------

_BOOL_TRUE = {"true", "True", "TRUE"}
_BOOL_FALSE = {"false", "False", "FALSE"}
_NULLS = {"", "~", "null", "Null", "NULL"}
_INT_RE = re.compile(r"^[-+]?\d+$")
_FLOAT_RE = re.compile(r"^[-+]?(\d+\.\d*|\.\d+|\d+)([eE][-+]?\d+)?$")
_BLOCK_HEADER_RE = re.compile(r"^([|>])([0-9])?([+-])?([0-9])?\s*(?:#.*)?$")


def _resolve_scalar(text: str) -> Any:
    if text in _NULLS:
        return None
    if text in _BOOL_TRUE:
        return True
    if text in _BOOL_FALSE:
        return False
    if _INT_RE.match(text):
        return int(text)
    if _FLOAT_RE.match(text) and not _INT_RE.match(text):
        return float(text)
    return text


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


class _Parser:
    def __init__(self, text: str):
        self.lines = text.split("\n")
        self.i = 0
        self._seen_doc = False

    # -- line utilities ------------------------------------------------------

    def _err(self, message: str) -> ParseError:
        return ParseError("YAML line %d: %s" % (self.i + 1, message))

    def _indent(self, line: str) -> int:
        stripped = line.lstrip(" ")
        if stripped.startswith("\t"):
            raise self._err("tabs are not allowed for indentation")
        return len(line) - len(stripped)

    def _skip_insignificant(self) -> None:
        """Advance past blank lines, comments, and document markers."""
        while self.i < len(self.lines):
            stripped = self.lines[self.i].strip()
            if stripped == "" or stripped.startswith("#"):
                self.i += 1
            elif stripped == "---" and self._indent(self.lines[self.i]) == 0:
                if self._seen_doc:
                    raise self._err("multi-document streams are not supported")
                self._seen_doc = True
                self.i += 1
            elif stripped == "..." and self._indent(self.lines[self.i]) == 0:
                self.i = len(self.lines)
            else:
                return

    def _at_end(self) -> bool:
        self._skip_insignificant()
        return self.i >= len(self.lines)

    # -- entry point -----------------------------------------------------------

    def parse(self) -> Any:
        if self._at_end():
            return None
        value = self._parse_node(0)
        if not self._at_end():
            raise self._err("unexpected content after the document root")
        return value

    # -- structure -------------------------------------------------------------

    def _split_key(self, content: str) -> Optional[Tuple[str, int]]:
        """If ``content`` opens a mapping entry, return (key, offset of the
        inline value within ``content``); otherwise ``None``."""
        if content.startswith(("'", '"')):
            quote = content[0]
            j = 1
            while j < len(content):
                if content[j] == quote:
                    if quote == "'" and content[j : j + 2] == "''":
                        j += 2
                        continue
                    break
                if quote == '"' and content[j] == "\\":
                    j += 1
                j += 1
            else:
                return None
            after = content[j + 1 :]
            trimmed = after.lstrip(" ")
            if trimmed.startswith(":") and (len(trimmed) == 1 or trimmed[1] == " "):
                colon = j + 1 + (len(after) - len(trimmed))
                key = _scan_quoted_string(content[: j + 1], 0, self._err)[0]
                return key, _skip_spaces(content, colon + 1)
            return None
        depth = 0
        for j, ch in enumerate(content):
            if ch in "[{":
                depth += 1
            elif ch in "]}":
                depth -= 1
            elif ch == ":" and depth == 0:
                if j + 1 == len(content) or content[j + 1] == " ":
                    key = content[:j].strip()
                    if not key or key.startswith("#"):
                        return None
                    return key, _skip_spaces(content, j + 1)
        return None

    def _parse_node(self, min_indent: int) -> Any:
        self._skip_insignificant()
        if self.i >= len(self.lines):
            return None
        line = self.lines[self.i]
        indent = self._indent(line)
        if indent < min_indent:
            return None
        content = line.strip()
        if content == "-" or content.startswith("- "):
            return self._parse_sequence(indent)
        if self._split_key(content) is not None:
            return self._parse_mapping(indent)
        return self._parse_scalar_value(indent, min_indent)

    def _parse_sequence(self, indent: int) -> List[Any]:
        items: List[Any] = []
        while not self._at_end():
            line = self.lines[self.i]
            ind = self._indent(line)
            if ind < indent:
                break
            content = line.strip()
            if not (content == "-" or content.startswith("- ")):
                break
            if ind > indent:
                raise self._err("unexpected indentation inside a sequence")
            if content == "-":
                self.i += 1
                items.append(self._parse_node(indent + 1))
            else:
                rest_col = _skip_spaces(line, ind + 1)
                # Rewrite the entry as a virtual line so nested content
                # (compact mappings, multi-line scalars) parses uniformly.
                self.lines[self.i] = " " * rest_col + line[rest_col:]
                items.append(self._parse_node(indent + 1))
        return items

    def _parse_mapping(self, indent: int) -> dict:
        out: dict = {}
        while not self._at_end():
            line = self.lines[self.i]
            ind = self._indent(line)
            if ind < indent:
                break
            content = line.strip()
            if content == "-" or content.startswith("- "):
                break
            if ind > indent:
                raise self._err("unexpected indentation inside a mapping")
            split = self._split_key(content)
            if split is None:
                raise self._err("expected a 'key: value' mapping entry")
            key, offset = split
            if key in out:
                raise self._err("duplicate mapping key %r" % key)
            rest = content[offset:]
            if rest == "" or rest.startswith("# "):
                self.i += 1
                value = self._parse_node(indent + 1)
                if value is None:
                    # Block sequences may sit at the key's own indentation —
                    # this is what PyYAML and psych actually emit.
                    self._skip_insignificant()
                    if self.i < len(self.lines):
                        nxt = self.lines[self.i]
                        nxt_content = nxt.strip()
                        if self._indent(nxt) == indent and (
                            nxt_content == "-" or nxt_content.startswith("- ")
                        ):
                            value = self._parse_sequence(indent)
                out[key] = value
            else:
                col = ind + offset
                self.lines[self.i] = " " * col + line[col:]
                out[key] = self._parse_scalar_value(col, indent + 1)
        return out

    # -- scalars ---------------------------------------------------------------

    def _parse_scalar_value(self, col: int, cont_indent: int) -> Any:
        line = self.lines[self.i]
        content = line[col:].strip()
        if content.startswith(("&", "*")):
            raise self._err("anchors and aliases are not supported")
        if content.startswith("!!binary"):
            return self._parse_binary(content, col, cont_indent)
        if content.startswith("!"):
            return self._parse_tagged(content, col, cont_indent)
        if content[:1] in ("|", ">") and _BLOCK_HEADER_RE.match(content):
            return self._parse_block_scalar(content, cont_indent - 1)
        if content[:1] in ("'", '"'):
            return self._parse_quoted(col)
        if content[:1] in ("{", "["):
            return self._parse_flow(col)
        return self._parse_plain(col, cont_indent)

    def _parse_binary(self, content: str, col: int, cont_indent: int) -> bytes:
        rest = content[len("!!binary") :].strip()
        if rest:
            self.lines[self.i] = " " * col + rest
            payload = self._parse_scalar_value(col, cont_indent)
        else:
            self.i += 1
            payload = self._parse_node(cont_indent)
        if not isinstance(payload, str):
            raise self._err("!!binary expects a base64 string payload")
        try:
            return base64.b64decode(re.sub(r"\s+", "", payload), validate=True)
        except Exception:
            raise self._err("invalid base64 in !!binary scalar")

    def _parse_tagged(self, content: str, col: int, cont_indent: int) -> Any:
        tag, _, rest = content.partition(" ")
        if tag not in ("!!str", "!!int", "!!float", "!!bool", "!!null"):
            raise self._err("unsupported YAML tag %r" % tag)
        self.lines[self.i] = " " * col + rest.strip()
        value = self._parse_scalar_value(col, cont_indent)
        if tag == "!!str":
            return "" if value is None else str(value)
        return value

    def _parse_block_scalar(self, header: str, key_indent: int) -> str:
        match = _BLOCK_HEADER_RE.match(header)
        assert match is not None
        style, chomp = match.group(1), match.group(3) or ""
        explicit = match.group(2) or match.group(4)
        self.i += 1
        block_indent = key_indent + int(explicit) if explicit else None
        collected: List[str] = []
        while self.i < len(self.lines):
            raw = self.lines[self.i]
            if raw.strip() == "":
                collected.append("")
                self.i += 1
                continue
            ind = self._indent(raw)
            if block_indent is None:
                if ind <= key_indent:
                    break
                block_indent = ind
            if ind < block_indent:
                break
            collected.append(raw[block_indent:])
            self.i += 1
        trailing = 0
        while collected and collected[-1] == "":
            collected.pop()
            trailing += 1
        if style == "|":
            body = "\n".join(collected)
        else:  # folded
            body = ""
            previous_blank = True
            for text in collected:
                if text == "":
                    body += "\n"
                    previous_blank = True
                elif previous_blank:
                    body += text
                    previous_blank = False
                else:
                    body += " " + text
        if chomp == "-":
            return body
        if chomp == "+":
            return body + "\n" * (trailing + 1)
        return body + "\n" if body else ""

    def _parse_quoted(self, col: int) -> str:
        line = self.lines[self.i]
        quote = line[col]
        pos = col + 1
        result: List[str] = []
        while True:
            fragment: List[str] = []
            closed = False
            escaped_break = False
            while pos < len(line):
                ch = line[pos]
                if ch == quote:
                    if quote == "'" and line[pos : pos + 2] == "''":
                        fragment.append("'")
                        pos += 2
                        continue
                    closed = True
                    pos += 1
                    break
                if quote == '"' and ch == "\\":
                    if pos + 1 >= len(line):
                        escaped_break = True
                        pos += 1
                        break
                    pos, decoded = _decode_escape(line, pos, self._err)
                    fragment.append(decoded)
                    continue
                fragment.append(ch)
                pos += 1
            piece = "".join(fragment)
            if closed:
                result.append(piece)
                trailer = line[pos:].strip()
                if trailer and not trailer.startswith("#"):
                    raise self._err("unexpected content after closing quote")
                self.i += 1
                return "".join(result)
            # The line ended before the closing quote: fold per YAML rules —
            # a break becomes a space, blank lines become newlines, and an
            # escaped break (double quotes, trailing backslash) glues.
            result.append(piece if escaped_break else piece.rstrip(" \t"))
            blanks = 0
            self.i += 1
            while self.i < len(self.lines) and self.lines[self.i].strip() == "":
                blanks += 1
                self.i += 1
            if self.i >= len(self.lines):
                raise self._err("unterminated quoted scalar")
            if blanks:
                result.append("\n" * blanks)
            elif not escaped_break:
                result.append(" ")
            line = self.lines[self.i]
            pos = _skip_spaces(line, 0)

    def _parse_plain(self, col: int, cont_indent: int) -> Any:
        line = self.lines[self.i]
        pieces: List[Tuple[int, str]] = [(0, _strip_plain_comment(line[col:].strip()))]
        self.i += 1
        blanks = 0
        while self.i < len(self.lines):
            raw = self.lines[self.i]
            stripped = raw.strip()
            if stripped == "":
                blanks += 1
                self.i += 1
                continue
            if self._indent(raw) < cont_indent:
                break
            if stripped.startswith("#"):
                self.i += 1
                continue
            if stripped == "-" or stripped.startswith("- "):
                break
            if self._split_key(stripped) is not None:
                break
            pieces.append((blanks, _strip_plain_comment(stripped)))
            blanks = 0
            self.i += 1
        self.i -= blanks  # trailing blank lines belong to the next node
        text = pieces[0][1]
        for breaks, piece in pieces[1:]:
            text += ("\n" * breaks if breaks else " ") + piece
        return _resolve_scalar(text)

    # -- flow collections --------------------------------------------------------

    def _parse_flow(self, col: int) -> Any:
        buffer = self.lines[self.i][col:].rstrip()
        while not _flow_balanced(buffer):
            self.i += 1
            if self.i >= len(self.lines):
                raise self._err("unterminated flow collection")
            buffer += " " + self.lines[self.i].strip()
        self.i += 1
        value, pos = _parse_flow_value(buffer, 0, self._err)
        trailer = buffer[pos:].strip()
        if trailer and not trailer.startswith("#"):
            raise self._err("unexpected content after flow collection")
        return value


def _skip_spaces(text: str, pos: int) -> int:
    while pos < len(text) and text[pos] == " ":
        pos += 1
    return pos


def _strip_plain_comment(text: str) -> str:
    match = re.search(r"\s#", text)
    return text[: match.start()].rstrip() if match else text


def _decode_escape(line: str, pos: int, err) -> Tuple[int, str]:
    """Decode a double-quote escape starting at ``line[pos] == '\\'``."""
    simple = {
        "0": "\x00", "a": "\a", "b": "\b", "t": "\t", "n": "\n", "v": "\v",
        "f": "\f", "r": "\r", "e": "\x1b", " ": " ", '"': '"', "\\": "\\",
        "/": "/", "_": " ", "N": "", "L": " ", "P": " ",
    }
    ch = line[pos + 1]
    if ch in simple:
        return pos + 2, simple[ch]
    widths = {"x": 2, "u": 4, "U": 8}
    if ch in widths:
        width = widths[ch]
        digits = line[pos + 2 : pos + 2 + width]
        if len(digits) != width:
            raise err("truncated \\%s escape" % ch)
        try:
            return pos + 2 + width, chr(int(digits, 16))
        except ValueError:
            raise err("invalid \\%s escape" % ch)
    raise err("unknown escape sequence \\%s" % ch)


def _flow_balanced(text: str) -> bool:
    depth = 0
    quote: Optional[str] = None
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == quote:
                if quote == "'" and text[i : i + 2] == "''":
                    i += 2
                    continue
                quote = None
            elif quote == '"' and ch == "\\":
                i += 1
        elif ch in "'\"":
            quote = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        i += 1
    return depth <= 0 and quote is None


def _parse_flow_value(text: str, pos: int, err) -> Tuple[Any, int]:
    pos = _skip_spaces(text, pos)
    if pos >= len(text):
        raise err("unexpected end of flow collection")
    ch = text[pos]
    if ch == "{":
        out: dict = {}
        pos = _skip_spaces(text, pos + 1)
        while pos < len(text) and text[pos] != "}":
            key, pos = _parse_flow_value(text, pos, err)
            pos = _skip_spaces(text, pos)
            if pos >= len(text) or text[pos] != ":":
                raise err("expected ':' in flow mapping")
            value, pos = _parse_flow_value(text, pos + 1, err)
            out[key if isinstance(key, str) else str(key)] = value
            pos = _skip_spaces(text, pos)
            if pos < len(text) and text[pos] == ",":
                pos = _skip_spaces(text, pos + 1)
        if pos >= len(text):
            raise err("unterminated flow mapping")
        return out, pos + 1
    if ch == "[":
        items: List[Any] = []
        pos = _skip_spaces(text, pos + 1)
        while pos < len(text) and text[pos] != "]":
            item, pos = _parse_flow_value(text, pos, err)
            items.append(item)
            pos = _skip_spaces(text, pos)
            if pos < len(text) and text[pos] == ",":
                pos = _skip_spaces(text, pos + 1)
        if pos >= len(text):
            raise err("unterminated flow sequence")
        return items, pos + 1
    if ch in "'\"":
        return _scan_quoted_string(text, pos, err)
    end = pos
    while end < len(text):
        c = text[end]
        if c in ",]}":
            break
        if c == ":" and (end + 1 >= len(text) or text[end + 1] in " ,]}"):
            break
        end += 1
    return _resolve_scalar(text[pos:end].strip()), end


def _scan_quoted_string(text: str, pos: int, err) -> Tuple[str, int]:
    quote = text[pos]
    pos += 1
    out: List[str] = []
    while pos < len(text):
        ch = text[pos]
        if ch == quote:
            if quote == "'" and text[pos : pos + 2] == "''":
                out.append("'")
                pos += 2
                continue
            return "".join(out), pos + 1
        if quote == '"' and ch == "\\":
            pos, decoded = _decode_escape(text, pos, err)
            out.append(decoded)
            continue
        out.append(ch)
        pos += 1
    raise err("unterminated quoted scalar")


def loads(text: str) -> Any:
    """Parse a YAML document (cassette subset) into Python data."""
    return _Parser(text).parse()


# ---------------------------------------------------------------------------
# Emitting
# ---------------------------------------------------------------------------

_PLAIN_SAFE_RE = re.compile(r"^[A-Za-z0-9_./][A-Za-z0-9_./+,;=@$%^()~ :?*&!<>-]*$")


def _needs_quotes(text: str) -> bool:
    if text == "" or text != text.strip():
        return True
    if not _PLAIN_SAFE_RE.match(text):
        return True
    if ": " in text or text.endswith(":") or " #" in text:
        return True
    if text[0] in "?*&!" and len(text) > 1 and text[1] == " ":
        return True
    # Anything the parser would resolve to a non-string must be quoted.
    return not isinstance(_resolve_scalar(text), str)


def _scalar_repr(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return json.dumps(value)
    text = str(value)
    if not _needs_quotes(text):
        return text
    if any(ord(ch) < 0x20 for ch in text):
        return json.dumps(text, ensure_ascii=False)
    return "'%s'" % text.replace("'", "''")


def _block_literal_ok(text: str) -> bool:
    """Whether a multi-line string round-trips through a ``|`` literal."""
    if "\n" not in text:
        return False
    if text.startswith(("\n", " ")) or text.endswith("\n\n"):
        return False
    body = text[:-1] if text.endswith("\n") else text
    for line in body.split("\n"):
        if line != line.rstrip(" ") or any(ord(ch) < 0x20 for ch in line):
            return False
    return True


def _emit(value: Any, indent: int, lines: List[str], sort_keys: bool) -> None:
    pad = " " * indent
    if isinstance(value, dict):
        if not value:
            lines.append(pad + "{}")
            return
        keys = sorted(value, key=str) if sort_keys else list(value)
        for key in keys:
            _emit_pair(str(key), value[key], indent, lines, sort_keys)
        return
    if isinstance(value, (list, tuple)):
        if not value:
            lines.append(pad + "[]")
            return
        for item in value:
            start = len(lines)
            _emit(item, indent + 2, lines, sort_keys)
            lines[start] = pad + "- " + lines[start][indent + 2 :]
        return
    if isinstance(value, bytes):
        lines.append(pad + "!!binary |")
        encoded = base64.b64encode(value).decode("ascii")
        for i in range(0, len(encoded), 72):
            lines.append(pad + "  " + encoded[i : i + 72])
        return
    if isinstance(value, str) and _block_literal_ok(value):
        chomp = "" if value.endswith("\n") else "-"
        body = value[:-1] if value.endswith("\n") else value
        lines.append(pad + "|" + chomp)
        for line in body.split("\n"):
            lines.append((pad + "  " + line) if line else "")
        return
    if isinstance(value, str) and "\n" in value:
        lines.append(pad + json.dumps(value, ensure_ascii=False))
        return
    lines.append(pad + _scalar_repr(value))


def _emit_pair(key: str, value: Any, indent: int, lines: List[str], sort_keys: bool) -> None:
    pad = " " * indent
    key_repr = key if not _needs_quotes(key) else "'%s'" % key.replace("'", "''")
    if isinstance(value, dict) and value:
        lines.append(pad + key_repr + ":")
        _emit(value, indent + 2, lines, sort_keys)
    elif isinstance(value, (list, tuple)) and len(value):
        lines.append(pad + key_repr + ":")
        _emit(value, indent, lines, sort_keys)  # dashes sit at the key column
    elif isinstance(value, bytes) or (isinstance(value, str) and _block_literal_ok(value)):
        start = len(lines)
        _emit(value, indent, lines, sort_keys)
        lines[start] = pad + key_repr + ": " + lines[start][indent:]
    else:
        single: List[str] = []
        _emit(value, 0, single, sort_keys)
        lines.append(pad + key_repr + ": " + single[0])


def dumps(value: Any, sort_keys: bool = True, explicit_start: bool = False) -> str:
    """Serialize Python data to cassette-style block YAML."""
    lines: List[str] = []
    if explicit_start:
        lines.append("---")
    _emit(value, 0, lines, sort_keys)
    return "\n".join(lines) + "\n"
