"""Format registry: detection, loading, and dumping by format id.

Detection is structural, not extension-based — a ``.json`` file can be any
of HAR, WireMock, nock, or a vcrpy JSON cassette. Each codec inspects the
parsed shape (root keys, entry fields) and claims the input or passes.
Detection order puts the most distinctive shapes first, so an ambiguous
input fails loudly instead of being misread.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, NamedTuple, Optional

from .errors import DetectError
from .formats import har, nock, vcr, wiremock
from .model import Fixture


class FormatSpec(NamedTuple):
    """A registered fixture dialect."""

    id: str
    title: str
    encoding: str
    load: Callable[..., Fixture]
    dump: Callable[..., str]
    dump_options: dict
    notes: str


FORMATS: Dict[str, FormatSpec] = {}


def _register(spec: FormatSpec) -> None:
    FORMATS[spec.id] = spec


_register(
    FormatSpec(
        id="har",
        title="HAR 1.2 (HTTP Archive)",
        encoding="json",
        load=har.load,
        dump=har.dump,
        dump_options={},
        notes="DevTools/proxy captures; timings and cookies are HAR-only",
    )
)
_register(
    FormatSpec(
        id="vcr",
        title="VCR cassette (Python vcrpy)",
        encoding="yaml or json",
        load=vcr.load,
        dump=vcr.dump,
        dump_options={"dialect": "python"},
        notes="root key 'interactions'; --vcr-serializer picks yaml/json",
    )
)
_register(
    FormatSpec(
        id="vcr-ruby",
        title="VCR cassette (Ruby VCR)",
        encoding="yaml or json",
        load=vcr.load,
        dump=vcr.dump,
        dump_options={"dialect": "ruby"},
        notes="root key 'http_interactions'; records recorded_at timestamps",
    )
)
_register(
    FormatSpec(
        id="wiremock",
        title="WireMock stub mapping(s)",
        encoding="json",
        load=wiremock.load,
        dump=wiremock.dump,
        dump_options={},
        notes="hostless stubs; --base-url supplies the origin when reading",
    )
)
_register(
    FormatSpec(
        id="nock",
        title="nock definitions (nock.define)",
        encoding="json",
        load=nock.load,
        dump=nock.dump,
        dump_options={},
        notes="array of scope/path definitions from nock.recorder",
    )
)

# Detection order: shapes with unmistakable root keys first.
_DETECTORS = (har.detect, vcr.detect, wiremock.detect, nock.detect)


def detect_format(text: str) -> str:
    """Identify the fixture dialect of ``text`` or raise :class:`DetectError`."""
    data: Any = None
    stripped = text.lstrip()
    if stripped.startswith(("{", "[")):
        try:
            data = json.loads(text)
        except ValueError:
            data = None
    for detector in _DETECTORS:
        result = detector(text, data)
        if result:
            return result
    raise DetectError(
        "could not detect the fixture format (expected HAR, VCR cassette, "
        "WireMock stub mapping, or nock definitions)"
    )


def get_format(format_id: str) -> FormatSpec:
    try:
        return FORMATS[format_id]
    except KeyError:
        known = ", ".join(sorted(FORMATS))
        raise DetectError("unknown format %r (known formats: %s)" % (format_id, known))


def load_fixture(
    text: str,
    format_id: Optional[str] = None,
    notes: Optional[List[str]] = None,
    base_url: Optional[str] = None,
) -> Fixture:
    """Parse fixture text, auto-detecting the format unless one is forced."""
    if notes is None:
        notes = []
    resolved = format_id or detect_format(text)
    spec = get_format(resolved)
    fixture = spec.load(text, notes=notes, base_url=base_url)
    fixture.meta.setdefault("source_format", resolved)
    return fixture


def dump_fixture(
    fixture: Fixture,
    format_id: str,
    notes: Optional[List[str]] = None,
    strict: bool = False,
    **options: Any,
) -> str:
    """Serialize a fixture into the named format."""
    if notes is None:
        notes = []
    spec = get_format(format_id)
    merged = dict(spec.dump_options)
    merged.update(options)
    return spec.dump(fixture, notes=notes, strict=strict, **merged)
