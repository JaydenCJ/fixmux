#!/usr/bin/env bash
# Smoke test for fixmux: convert the example HAR capture through every
# dialect, verify semantic equivalence after each hop, and check that drift
# and strict mode fail loudly. Self-contained: pure stdlib, no network,
# idempotent (works from a clean tree, writes only to a temp dir).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-python3}"
if [ -x "$ROOT/.venv/bin/python" ]; then
  PYTHON="$ROOT/.venv/bin/python"
fi

# The package has zero runtime dependencies, so running from src/ needs no install.
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/fixmux-smoke.XXXXXX")"
trap 'rm -rf "$WORKDIR"' EXIT

fail() { echo "SMOKE FAIL: $1" >&2; exit 1; }

echo "[smoke] python: $("$PYTHON" --version 2>&1)"
HAR="$ROOT/examples/capture.har"

# 1. Detection identifies the example capture with the right exchange count.
detect_out="$("$PYTHON" -m fixmux detect "$HAR")"
echo "$detect_out" | sed 's/^/[detect] /'
echo "$detect_out" | grep -q "har	2 exchanges" || fail "detect did not report har with 2 exchanges"

# 2. HAR -> vcrpy cassette (YAML), shape-checked.
"$PYTHON" -m fixmux convert "$HAR" -t vcr -o "$WORKDIR/cassette.yml" 2>/dev/null
grep -q "^interactions:" "$WORKDIR/cassette.yml" || fail "vcr cassette missing interactions key"
grep -q "uri: http://api.example.test/v1/members?page=2" "$WORKDIR/cassette.yml" \
  || fail "vcr cassette missing the recorded uri"

# 3. HAR -> Ruby VCR cassette: dialect shape and timestamp survival.
#    (The vcrpy dialect has no timestamp field, so this hop starts from HAR.)
"$PYTHON" -m fixmux convert "$HAR" -t vcr-ruby -o "$WORKDIR/cassette-ruby.yml" 2>/dev/null
head -1 "$WORKDIR/cassette-ruby.yml" | grep -q -- "---" || fail "ruby cassette missing document start"
grep -q "^http_interactions:" "$WORKDIR/cassette-ruby.yml" || fail "ruby cassette missing http_interactions"
grep -q "recorded_at: Sun, 01 Mar 2026 09:30:00 GMT" "$WORKDIR/cassette-ruby.yml" \
  || fail "ruby cassette lost the recording timestamp"

# 4. Ruby cassette -> nock definitions, then verify against the original HAR.
"$PYTHON" -m fixmux convert "$WORKDIR/cassette-ruby.yml" -t nock -o "$WORKDIR/nock.json" 2>/dev/null
verify_out="$("$PYTHON" -m fixmux verify "$HAR" "$WORKDIR/nock.json" 2>/dev/null)"
echo "$verify_out" | sed 's/^/[verify] /'
echo "$verify_out" | grep -q "equivalent: 2 exchanges" \
  || fail "HAR -> vcr -> vcr-ruby -> nock chain was not equivalent to the source"

# 5. HAR -> WireMock stubs: host drop is noted, verify still passes (path+query).
wiremock_err="$("$PYTHON" -m fixmux convert "$HAR" -t wiremock -o "$WORKDIR/stubs.json" 2>&1 >/dev/null)"
echo "$wiremock_err" | grep -q "carry no host" || fail "wiremock conversion did not note the dropped host"
"$PYTHON" -m fixmux verify "$HAR" "$WORKDIR/stubs.json" >/dev/null 2>&1 \
  || fail "verify against WireMock stubs should pass (host ignored)"

# 6. Strict mode turns the same lossy conversion into exit code 2.
set +e
"$PYTHON" -m fixmux convert "$HAR" -t wiremock --strict >/dev/null 2>&1
strict_rc=$?
set -e
[ "$strict_rc" -eq 2 ] || fail "strict lossy conversion should exit 2, got $strict_rc"

# 7. Drift detection: flip a status code, verify must exit 1 and say why.
sed 's/"status": 201/"status": 503/' "$HAR" > "$WORKDIR/drifted.har"
set +e
drift_out="$("$PYTHON" -m fixmux verify "$HAR" "$WORKDIR/drifted.har" 2>/dev/null)"
drift_rc=$?
set -e
[ "$drift_rc" -eq 1 ] || fail "verify on drifted fixture should exit 1, got $drift_rc"
echo "$drift_out" | grep -q "response status differs" || fail "verify did not name the drifted field"
echo "$drift_out" | sed 's/^/[drift] /'

# 8. --version agrees with the package, --help lists the subcommands.
version_out="$("$PYTHON" -m fixmux --version)"
pkg_version="$("$PYTHON" -c 'import fixmux; print(fixmux.__version__)')"
[ "$version_out" = "fixmux $pkg_version" ] \
  || fail "--version mismatch: '$version_out' vs package '$pkg_version'"
"$PYTHON" -m fixmux --help | grep -q "convert" || fail "--help missing convert command"

echo "SMOKE OK"
