"""Semantic equivalence checking between two fixtures.

``fixmux verify`` uses this after a conversion to prove nothing was lost.
Byte comparison would be meaningless across dialects — header casing, JSON
key order, and default ports all differ legitimately — so exchanges are
compared on a canonical form:

* method uppercased, URL normalized (lowercase scheme/host, default port
  stripped, ``/`` for an empty path);
* headers grouped per RFC 7230 (duplicates comma-joined, names lowered);
* bodies by bytes, falling back to parsed-JSON equality when both sides
  are JSON (nock stores JSON values, so key order is not preserved);
* status codes (status *text* is not compared: several formats drop it).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple

from .model import Body, Exchange, Fixture, normalize_url, split_url


@dataclass
class Difference:
    """One human-readable mismatch found between two fixtures."""

    index: int
    field: str
    left: str
    right: str

    def render(self) -> str:
        return "exchange %d: %s differs\n  left:  %s\n  right: %s" % (
            self.index,
            self.field,
            self.left,
            self.right,
        )


@dataclass
class ComparisonResult:
    differences: List[Difference] = field(default_factory=list)
    compared: int = 0

    @property
    def equivalent(self) -> bool:
        return not self.differences


def compare_fixtures(
    left: Fixture, right: Fixture, ignore_host: bool = False
) -> ComparisonResult:
    """Compare two fixtures exchange-by-exchange, in order.

    ``ignore_host`` compares only path+query — needed when one side passed
    through WireMock, which cannot carry an origin.
    """
    result = ComparisonResult(compared=max(len(left.exchanges), len(right.exchanges)))
    if len(left.exchanges) != len(right.exchanges):
        result.differences.append(
            Difference(
                index=-1,
                field="exchange count",
                left=str(len(left.exchanges)),
                right=str(len(right.exchanges)),
            )
        )
        return result
    for index, (a, b) in enumerate(zip(left.exchanges, right.exchanges)):
        result.differences.extend(_compare_exchange(a, b, index, ignore_host))
    return result


def _compare_exchange(
    a: Exchange, b: Exchange, index: int, ignore_host: bool
) -> List[Difference]:
    diffs: List[Difference] = []
    if a.request.method.upper() != b.request.method.upper():
        diffs.append(Difference(index, "request method", a.request.method, b.request.method))
    url_a, url_b = _comparable_url(a.request.url, ignore_host), _comparable_url(
        b.request.url, ignore_host
    )
    if url_a != url_b:
        diffs.append(Difference(index, "request url", url_a, url_b))
    diffs.extend(_compare_headers(a.request.headers, b.request.headers, index, "request"))
    diffs.extend(_compare_body(a.request.body, b.request.body, index, "request"))
    if a.response.status != b.response.status:
        diffs.append(
            Difference(index, "response status", str(a.response.status), str(b.response.status))
        )
    diffs.extend(_compare_headers(a.response.headers, b.response.headers, index, "response"))
    diffs.extend(_compare_body(a.response.body, b.response.body, index, "response"))
    return diffs


def _comparable_url(url: str, ignore_host: bool) -> str:
    if ignore_host:
        _, _, _, path, query = split_url(url)
        return (path or "/") + (("?" + query) if query else "")
    return normalize_url(url)


def _canonical_headers(headers) -> List[Tuple[str, str]]:
    # RFC 7230 §3.2.2: repeated fields are equivalent to one field whose
    # value is the comma-joined list. WireMock stores duplicates joined, VCR
    # stores them as lists — canonicalize both to the joined form.
    grouped: dict = {}
    for name, value in headers:
        grouped.setdefault(name.lower(), []).append(value)
    return sorted((name, ", ".join(values)) for name, values in grouped.items())


def _compare_headers(a, b, index: int, side: str) -> List[Difference]:
    canon_a, canon_b = _canonical_headers(a), _canonical_headers(b)
    if canon_a == canon_b:
        return []
    only_a = [h for h in canon_a if h not in canon_b]
    only_b = [h for h in canon_b if h not in canon_a]
    return [
        Difference(
            index,
            "%s headers" % side,
            _render_headers(only_a) or "(none extra)",
            _render_headers(only_b) or "(none extra)",
        )
    ]


def _render_headers(headers: List[Tuple[str, str]]) -> str:
    return "; ".join("%s: %s" % (name, value) for name, value in headers)


def _compare_body(a: Body, b: Body, index: int, side: str) -> List[Difference]:
    if a.is_empty and b.is_empty:
        return []
    bytes_a, bytes_b = a.to_bytes(), b.to_bytes()
    if bytes_a == bytes_b:
        return []
    json_a, json_b = a.json(), b.json()
    if json_a is not None and json_b is not None and json_a == json_b:
        return []
    return [
        Difference(
            index,
            "%s body" % side,
            _render_body(a, bytes_a),
            _render_body(b, bytes_b),
        )
    ]


def _render_body(body: Body, raw: bytes, limit: int = 120) -> str:
    if body.is_empty:
        return "(empty)"
    if body.text is not None:
        text = body.text
        return text if len(text) <= limit else text[:limit] + "…"
    return "(binary, %d bytes)" % len(raw)
