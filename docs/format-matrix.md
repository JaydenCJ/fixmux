# The fixture format matrix

fixmux converts through a small intermediate model — an ordered list of
exchanges, each with a request (method, full URL, ordered multi-valued
headers, text/binary body) and a response (status, status text, headers,
body), plus a per-exchange recording timestamp. This document is the honest
map of what each dialect can and cannot carry.

## Field support by format

| Exchange field | HAR 1.2 | vcr (vcrpy) | vcr-ruby | WireMock | nock |
|---|---|---|---|---|---|
| Method | yes | yes (upper) | yes (lower) | yes | yes |
| Full URL incl. host | yes | yes | yes | **no** — path+query only | yes (`scope` + `path`) |
| Query string | in URL + `queryString` | in URI | in URI | in `url` / `queryParameters` | in `path` |
| Request headers (multi-valued) | ordered list | name → value list | name → value list | joined per RFC 7230 into `equalTo` | `reqheaders` |
| Request body (text) | `postData.text` | bare string | `{encoding, string}` | `bodyPatterns[equalTo]` | string / parsed JSON |
| Request body (binary) | base64 + `_fixmuxEncoding` marker | YAML `!!binary` | `base64_string` | `binaryEqualTo` | hex (no flag — noted) |
| Response status / text | yes / yes | yes / yes | yes / yes | yes / `statusMessage` | yes / **no** |
| Response headers (dupes) | ordered list | name → value list | name → value list | value lists | `rawHeaders` (exact order) |
| Response body (text) | `content.text` | `{string}` | `{encoding, string}` | `body` / `jsonBody` | string / parsed JSON |
| Response body (binary) | base64 `encoding` | YAML `!!binary` | `base64_string` | `base64Body` | hex + `responseIsBinary` |
| Recorded-at timestamp | `startedDateTime` (ISO 8601) | **no** | `recorded_at` (RFC 2822) | **no** | **no** |
| Timings, cookies-as-objects, cache, pages | yes (HAR-only) | no | no | no | no |

## What "lossless" means here

- **Within the shared exchange model, conversion is lossless.** Method, URL,
  query, all header values, text and binary bodies, and status codes survive
  every path through the matrix; `fixmux verify` proves it per file.
- **Format-specific extras degrade explicitly, never silently.** Everything
  a target cannot represent is either a warning note on stderr (default) or
  a hard failure (`--strict`). The known cases:
  - HAR-only telemetry (`time`, `timings`, `cache`, `pages`, …) has no
    equivalent anywhere — noted when loading a HAR that carries it.
  - WireMock stubs are hostless; the origin is noted (or fails `--strict`)
    when writing stubs and reattached with `--base-url` when reading them.
  - WireMock regex/partial matchers (`urlPattern`, `matches`, `contains`)
    describe *sets* of requests, not recorded ones — noted, kept as text.
  - nock and vcrpy have no per-exchange timestamp field; recording times
    survive only on paths through HAR and vcr-ruby.
  - vcrpy's JSON serializer cannot hold binary bodies (vcrpy itself refuses
    to record them) — fixmux writes a `base64_string` field and notes it.
- **Deterministic output.** The same input always produces byte-identical
  output: keys are sorted where the native tools sort them, sizes are
  computed, and missing timestamps become the fixed epoch sentinel rather
  than "now".

## Comparison semantics (`fixmux verify`)

Byte equality is the wrong bar across dialects, so verify canonicalizes:

| Aspect | Rule |
|---|---|
| URL | lowercase scheme/host, default port stripped, `/` for empty path |
| Headers | names lowercased, duplicates comma-joined (RFC 7230 §3.2.2) |
| Bodies | byte equality, else parsed-JSON equality when both sides parse |
| Status text | not compared — WireMock and nock do not store it |
| Host | ignored automatically when exactly one side is WireMock |
