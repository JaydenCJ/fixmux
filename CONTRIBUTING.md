# Contributing to fixmux

Thanks for your interest in contributing. Issues, discussions, and pull
requests are all welcome.

## Getting started

You need Python 3.9 or newer — nothing else; the runtime has zero
dependencies and the tests need only pytest.

```bash
git clone https://github.com/JaydenCJ/fixmux
cd fixmux
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
bash scripts/smoke.sh
```

`scripts/smoke.sh` drives the real CLI end-to-end — detection, a
four-format conversion chain, `verify` on both an equivalent pair and a
drifted one, and strict-mode failure — and must print `SMOKE OK`.

## Before you open a pull request

1. `pytest` — the whole suite must pass, fully offline.
2. `bash scripts/smoke.sh` — must print `SMOKE OK`.
3. Add tests for behavior changes; codec logic stays in pure, unit-testable
   modules under `src/fixmux/formats/`.
4. Keep the three READMEs aligned — `README.md`, `README.zh.md`, and
   `README.ja.md` are line-for-line translations; update all three together
   (English is authoritative).

## Ground rules

- **No new runtime dependencies.** Reading VCR YAML without PyYAML is the
  point of `yamlite.py`; adding a dependency needs a very good reason in
  the PR description.
- **Never guess at fixture data.** Anything a target format cannot
  represent must surface as a note (default) or an
  `UnsupportedFeatureError` (`--strict`) — silent loss is a bug even when
  the output "works".
- **Deterministic output.** Converting the same input twice must produce
  byte-identical bytes: no wall-clock timestamps, no random ordering.
- **New dialects follow the codec contract** documented in
  `src/fixmux/formats/__init__.py` (`detect` / `load` / `dump`), plus a row
  in `docs/format-matrix.md` and round-trip coverage in
  `tests/test_convert.py`.
- Code comments and doc comments are written in English.

## Reporting bugs

Please include the smallest fixture that reproduces the problem (scrub
credentials first!), the exact command line, `fixmux --version` output, and
what you expected the converted fixture to contain. For a bad conversion,
the output of `fixmux verify original converted` usually pinpoints the
field.

## Security

Do not open public issues for vulnerabilities; use GitHub private
vulnerability reporting on the repository instead.
