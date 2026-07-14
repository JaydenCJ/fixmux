"""Command-line interface for fixmux.

Subcommands::

    fixmux convert INPUT -t FORMAT [-o OUT]   convert between fixture dialects
    fixmux detect INPUT [INPUT ...]           identify format + exchange count
    fixmux inspect INPUT                      per-exchange summary table
    fixmux verify LEFT RIGHT                  semantic equivalence (exit 1 on drift)
    fixmux formats                            list supported dialects

Exit codes: 0 success, 1 ``verify`` found differences, 2 any error (bad
input, unknown format, strict-mode loss). Warnings about best-effort
decisions go to stderr so stdout stays pipeable.
"""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from . import __version__, convert
from .compare import compare_fixtures
from .errors import FixmuxError
from .model import Fixture
from .registry import FORMATS, detect_format, load_fixture


def _read_input(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _emit_notes(notes: List[str]) -> None:
    for note in notes:
        print("fixmux: note: %s" % note, file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fixmux",
        description="Losslessly convert HTTP fixtures between HAR, VCR cassette, "
        "WireMock, and nock formats.",
    )
    parser.add_argument(
        "--version", action="version", version="fixmux %s" % __version__
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    p_convert = sub.add_parser("convert", help="convert a fixture to another format")
    p_convert.add_argument("input", help="input file, or '-' for stdin")
    p_convert.add_argument(
        "-t", "--to", required=True, metavar="FORMAT",
        help="target format (%s)" % ", ".join(sorted(FORMATS)),
    )
    p_convert.add_argument(
        "-f", "--from", dest="from_format", metavar="FORMAT",
        help="source format (default: auto-detect)",
    )
    p_convert.add_argument(
        "-o", "--output", metavar="FILE", help="output file (default: stdout)"
    )
    p_convert.add_argument(
        "--strict", action="store_true",
        help="fail instead of degrading when the target cannot represent something",
    )
    p_convert.add_argument(
        "--base-url", metavar="URL",
        help="origin to assume when the source format has no host (WireMock)",
    )
    p_convert.add_argument(
        "--vcr-serializer", choices=("yaml", "json"), default="yaml",
        help="cassette encoding when the target is vcr/vcr-ruby (default: yaml)",
    )

    p_detect = sub.add_parser("detect", help="identify the format of fixture files")
    p_detect.add_argument("inputs", nargs="+", help="files to inspect, or '-' for stdin")

    p_inspect = sub.add_parser("inspect", help="summarize the exchanges in a fixture")
    p_inspect.add_argument("input", help="input file, or '-' for stdin")
    p_inspect.add_argument(
        "-f", "--from", dest="from_format", metavar="FORMAT",
        help="source format (default: auto-detect)",
    )
    p_inspect.add_argument(
        "--base-url", metavar="URL",
        help="origin to assume when the source format has no host (WireMock)",
    )

    p_verify = sub.add_parser(
        "verify", help="check that two fixtures record the same exchanges"
    )
    p_verify.add_argument("left", help="first fixture file")
    p_verify.add_argument("right", help="second fixture file")
    p_verify.add_argument(
        "--base-url", metavar="URL",
        help="origin to assume for a side that has no host (WireMock)",
    )
    p_verify.add_argument(
        "--ignore-host", action="store_true",
        help="compare path+query only (for fixtures that passed through WireMock)",
    )

    sub.add_parser("formats", help="list the supported fixture formats")
    return parser


# ---------------------------------------------------------------------------
# Subcommand implementations
# ---------------------------------------------------------------------------


def _cmd_convert(args: argparse.Namespace) -> int:
    text = _read_input(args.input)
    options = {}
    if args.to in ("vcr", "vcr-ruby"):
        options["serializer"] = args.vcr_serializer
    result = convert(
        text,
        to_format=args.to,
        from_format=args.from_format,
        strict=args.strict,
        base_url=args.base_url,
        **options,
    )
    _emit_notes(result.notes)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(result.text)
        print(
            "fixmux: %s -> %s: wrote %d exchange%s to %s"
            % (
                result.from_format,
                result.to_format,
                result.exchanges,
                "" if result.exchanges == 1 else "s",
                args.output,
            ),
            file=sys.stderr,
        )
    else:
        sys.stdout.write(result.text)
    return 0


def _cmd_detect(args: argparse.Namespace) -> int:
    failures = 0
    for path in args.inputs:
        try:
            text = _read_input(path)
            format_id = detect_format(text)
            fixture = load_fixture(text, format_id, notes=[])
            print(
                "%s\t%s\t%d exchange%s"
                % (path, format_id, len(fixture.exchanges), "" if len(fixture.exchanges) == 1 else "s")
            )
        except (OSError, FixmuxError) as exc:
            print("%s\terror\t%s" % (path, exc), file=sys.stderr)
            failures += 1
    return 2 if failures else 0


def _cmd_inspect(args: argparse.Namespace) -> int:
    text = _read_input(args.input)
    notes: List[str] = []
    format_id = args.from_format or detect_format(text)
    fixture = load_fixture(text, format_id, notes=notes, base_url=args.base_url)
    _emit_notes(notes)
    print("format: %s" % format_id)
    print("exchanges: %d" % len(fixture.exchanges))
    if fixture.meta.get("recorded_with"):
        print("recorded with: %s" % fixture.meta["recorded_with"])
    for index, exchange in enumerate(fixture.exchanges):
        print(_summarize(index, exchange))
    return 0


def _summarize(index: int, exchange) -> str:
    request, response = exchange.request, exchange.response
    body = response.body
    if body.is_empty:
        size = "empty"
    else:
        size = "%d bytes" % len(body.to_bytes())
        if body.base64 is not None:
            size += ", binary"
    mime = body.mime or "-"
    return "%3d. %s %s -> %d (%s, %s)" % (
        index,
        request.method,
        request.url,
        response.status,
        mime,
        size,
    )


def _cmd_verify(args: argparse.Namespace) -> int:
    left_text, right_text = _read_input(args.left), _read_input(args.right)
    notes: List[str] = []
    left = _load_for_verify(left_text, args.base_url, notes)
    right = _load_for_verify(right_text, args.base_url, notes)
    _emit_notes(notes)
    ignore_host = args.ignore_host or _involves_wiremock(left, right)
    result = compare_fixtures(left, right, ignore_host=ignore_host)
    if result.equivalent:
        print(
            "equivalent: %d exchange%s"
            % (result.compared, "" if result.compared == 1 else "s")
        )
        return 0
    for difference in result.differences:
        print(difference.render())
    print("not equivalent: %d difference%s" % (
        len(result.differences), "" if len(result.differences) == 1 else "s"
    ))
    return 1


def _load_for_verify(text: str, base_url: Optional[str], notes: List[str]) -> Fixture:
    return load_fixture(text, detect_format(text), notes=notes, base_url=base_url)


def _involves_wiremock(left: Fixture, right: Fixture) -> bool:
    formats = {left.meta.get("source_format"), right.meta.get("source_format")}
    return "wiremock" in formats and len(formats) > 1


def _cmd_formats(args: argparse.Namespace) -> int:
    header = ("ID", "ENCODING", "TITLE", "NOTES")
    rows = [
        (spec.id, spec.encoding, spec.title, spec.notes)
        for spec in (FORMATS[key] for key in sorted(FORMATS))
    ]
    widths = [max(len(row[i]) for row in rows + [header]) for i in range(3)]
    line = "%-*s  %-*s  %-*s  %s"
    print(line % (widths[0], header[0], widths[1], header[1], widths[2], header[2], header[3]))
    for row in rows:
        print(line % (widths[0], row[0], widths[1], row[1], widths[2], row[2], row[3]))
    return 0


_COMMANDS = {
    "convert": _cmd_convert,
    "detect": _cmd_detect,
    "inspect": _cmd_inspect,
    "verify": _cmd_verify,
    "formats": _cmd_formats,
}


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    try:
        return _COMMANDS[args.command](args)
    except FixmuxError as exc:
        print("fixmux: error: %s" % exc, file=sys.stderr)
        return 2
    except OSError as exc:
        print("fixmux: error: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
