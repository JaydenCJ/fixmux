"""fixmux — losslessly convert HTTP fixtures between HAR, VCR cassette,
WireMock, and nock formats.

Public API::

    import fixmux

    fixture = fixmux.load_fixture(open("capture.har").read())
    text = fixmux.dump_fixture(fixture, "nock")

or, in one step with detection and warnings::

    result = fixmux.convert(har_text, to_format="wiremock")
    print(result.text)          # the converted fixture
    print(result.notes)         # anything that could not carry over
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional

from .compare import ComparisonResult, compare_fixtures
from .errors import DetectError, FixmuxError, ParseError, UnsupportedFeatureError
from .model import Body, Exchange, Fixture, Request, Response
from .registry import FORMATS, detect_format, dump_fixture, load_fixture

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "Body",
    "ComparisonResult",
    "Conversion",
    "DetectError",
    "Exchange",
    "Fixture",
    "FixmuxError",
    "FORMATS",
    "ParseError",
    "Request",
    "Response",
    "UnsupportedFeatureError",
    "compare_fixtures",
    "convert",
    "detect_format",
    "dump_fixture",
    "load_fixture",
]


@dataclass
class Conversion:
    """The outcome of a one-shot :func:`convert` call."""

    text: str
    from_format: str
    to_format: str
    exchanges: int
    notes: List[str] = field(default_factory=list)


def convert(
    text: str,
    to_format: str,
    from_format: Optional[str] = None,
    strict: bool = False,
    base_url: Optional[str] = None,
    **dump_options: Any,
) -> Conversion:
    """Convert fixture text to ``to_format``, auto-detecting the source.

    In ``strict`` mode any construct the target format cannot represent
    raises :class:`UnsupportedFeatureError`; otherwise it is recorded in
    ``notes`` and a best-effort conversion is returned.
    """
    notes: List[str] = []
    resolved = from_format or detect_format(text)
    fixture = load_fixture(text, resolved, notes=notes, base_url=base_url)
    output = dump_fixture(fixture, to_format, notes=notes, strict=strict, **dump_options)
    return Conversion(
        text=output,
        from_format=resolved,
        to_format=to_format,
        exchanges=len(fixture.exchanges),
        notes=notes,
    )
