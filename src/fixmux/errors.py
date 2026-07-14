"""Exception hierarchy for fixmux.

Every error raised on purpose by fixmux derives from :class:`FixmuxError`, so
callers embedding the library can catch one type. The CLI maps them to exit
code 2 with a single-line message; parse errors carry enough location context
(format name, and line numbers for YAML) to find the offending input.
"""

from __future__ import annotations


class FixmuxError(Exception):
    """Base class for all fixmux errors."""


class ParseError(FixmuxError):
    """The input could not be parsed as the claimed (or detected) format."""


class DetectError(FixmuxError):
    """No known fixture format matched the input."""


class UnsupportedFeatureError(FixmuxError):
    """The input uses a feature the target format cannot represent.

    Raised only in ``--strict`` mode; the default (lenient) mode downgrades
    the same condition to a warning note and produces a best-effort output.
    """
