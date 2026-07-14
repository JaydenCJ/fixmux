"""The intermediate exchange model every format converts through.

fixmux never converts format-to-format directly. Each codec maps its dialect
onto this small, explicit model — a :class:`Fixture` holding ordered
:class:`Exchange` objects — and back. Anything all four formats can express
(method, full URL, ordered multi-valued headers, text or binary bodies,
status, status text, recording time) lives here as first-class data, so a
round trip through any pair of formats preserves it exactly.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import format_datetime, parsedate_to_datetime
from typing import List, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit

from .errors import ParseError

Headers = List[Tuple[str, str]]

_DEFAULT_PORTS = {"http": 80, "https": 443}

#: Deterministic placeholder used when a format requires a timestamp the
#: source never recorded. Fixed on purpose: converting the same input twice
#: must produce byte-identical output.
EPOCH_ISO = "1970-01-01T00:00:00+00:00"


@dataclass
class Body:
    """An HTTP message body, kept in whichever encoding the source used.

    Exactly one of ``text`` / ``base64`` is set for a non-empty body. Binary
    payloads stay base64-encoded end to end so no decode/re-encode step can
    corrupt them; ``to_bytes`` is only used for comparisons.
    """

    text: Optional[str] = None
    base64: Optional[str] = None
    mime: Optional[str] = None

    @property
    def is_empty(self) -> bool:
        return not self.text and not self.base64

    def to_bytes(self) -> bytes:
        if self.base64 is not None:
            try:
                return base64.b64decode(self.base64, validate=True)
            except (binascii.Error, ValueError):
                # Tolerate sloppy padding/whitespace from hand-edited fixtures.
                return base64.b64decode(self.base64 + "==")
        if self.text is not None:
            return self.text.encode("utf-8")
        return b""

    def json(self):
        """Parsed JSON value of a text body, or ``None`` if it is not JSON."""
        if not self.text:
            return None
        try:
            return json.loads(self.text)
        except ValueError:
            return None

    @classmethod
    def from_bytes(cls, raw: bytes, mime: Optional[str] = None) -> "Body":
        """Build a body from raw bytes, preferring text when it is valid UTF-8."""
        if not raw:
            return cls(mime=mime)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return cls(base64=base64.b64encode(raw).decode("ascii"), mime=mime)
        # NUL bytes decode fine but mark the payload as binary in practice.
        if "\x00" in text:
            return cls(base64=base64.b64encode(raw).decode("ascii"), mime=mime)
        return cls(text=text, mime=mime)


@dataclass
class Request:
    method: str = "GET"
    url: str = ""
    headers: Headers = field(default_factory=list)
    body: Body = field(default_factory=Body)


@dataclass
class Response:
    status: int = 200
    status_text: str = ""
    headers: Headers = field(default_factory=list)
    body: Body = field(default_factory=Body)


@dataclass
class Exchange:
    """One recorded request/response pair plus per-exchange metadata.

    ``meta`` currently carries ``recorded_at`` (ISO 8601 string) when the
    source format recorded one; codecs translate it to their native
    timestamp representation on output.
    """

    request: Request = field(default_factory=Request)
    response: Response = field(default_factory=Response)
    meta: dict = field(default_factory=dict)


@dataclass
class Fixture:
    """An ordered collection of exchanges — the unit fixmux converts."""

    exchanges: List[Exchange] = field(default_factory=list)
    meta: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Header helpers
# ---------------------------------------------------------------------------


def first_header(headers: Headers, name: str) -> Optional[str]:
    """First value for ``name``, case-insensitively, or ``None``."""
    lowered = name.lower()
    for key, value in headers:
        if key.lower() == lowered:
            return value
    return None


def header_multimap(headers: Headers) -> "dict[str, List[str]]":
    """Group values by original header name, preserving first-seen casing."""
    grouped: "dict[str, List[str]]" = {}
    casing: "dict[str, str]" = {}
    for key, value in headers:
        canonical = casing.setdefault(key.lower(), key)
        grouped.setdefault(canonical, []).append(value)
    return grouped


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------


def split_url(url: str) -> Tuple[str, str, Optional[int], str, str]:
    """Split a URL into (scheme, host, explicit port or None, path, query)."""
    parts = urlsplit(url)
    try:
        port = parts.port
    except ValueError:
        # A hand-edited fixture like "http://host:port/" must fail with a
        # fixmux error (CLI exit 2), not an uncaught traceback.
        raise ParseError("invalid port in URL %r" % url)
    return (
        parts.scheme.lower(),
        (parts.hostname or "").lower(),
        port,
        parts.path,
        parts.query,
    )


def normalize_url(url: str) -> str:
    """Canonical form used for comparisons: lowercase scheme/host, no default
    port, and ``/`` for an empty path. Query strings are kept verbatim
    because their order can be significant to the server under test."""
    scheme, host, port, path, query = split_url(url)
    if port is not None and _DEFAULT_PORTS.get(scheme) == port:
        port = None
    netloc = host if port is None else "%s:%d" % (host, port)
    return urlunsplit((scheme, netloc, path or "/", query, ""))


def origin(url: str, explicit_port: bool = False) -> str:
    """``scheme://host[:port]`` for a URL. With ``explicit_port`` the default
    port is spelled out (nock's recorder does this in ``scope``)."""
    scheme, host, port, _, _ = split_url(url)
    if port is None and explicit_port:
        port = _DEFAULT_PORTS.get(scheme)
    if port is None or (not explicit_port and _DEFAULT_PORTS.get(scheme) == port):
        return "%s://%s" % (scheme, host)
    return "%s://%s:%d" % (scheme, host, port)


def path_and_query(url: str) -> str:
    """``/path?query`` for a URL — the part WireMock and nock store."""
    _, _, _, path, query = split_url(url)
    out = path or "/"
    if query:
        out += "?" + query
    return out


# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------


def parse_when(value: str) -> Optional[str]:
    """Parse an ISO 8601 or RFC 2822 timestamp into a canonical ISO string.

    HAR uses ISO 8601; Ruby VCR uses RFC 2822. Returns ``None`` if the value
    is not a recognizable timestamp — the raw string is then kept in meta so
    nothing is silently discarded.
    """
    text = value.strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def to_iso(meta: dict) -> str:
    return meta.get("recorded_at") or EPOCH_ISO


def to_rfc2822(meta: dict) -> str:
    dt = datetime.fromisoformat(to_iso(meta))
    return format_datetime(dt.astimezone(timezone.utc), usegmt=True)
