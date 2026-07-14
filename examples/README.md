# fixmux examples

This directory holds the same two HTTP exchanges — a `GET /v1/members?page=2`
and a `POST /v1/members` — expressed in all four fixture dialects:

| File | Format | Typically consumed by |
|---|---|---|
| `capture.har` | HAR 1.2 | browser DevTools, proxies, `har-express-mock` |
| `cassette.yml` | VCR cassette (Ruby dialect) | Ruby `VCR`, and vcrpy after `-t vcr` |
| `nock-definitions.json` | nock definitions | JavaScript `nock.define()` / `nock.load()` |
| `wiremock-stubs.json` | WireMock stub mappings | `wiremock --root-dir`, `__admin/mappings` |

`capture.har` is the source of truth; the other three were generated from it
by fixmux itself:

```bash
fixmux convert examples/capture.har -t vcr-ruby -o examples/cassette.yml
fixmux convert examples/capture.har -t nock     -o examples/nock-definitions.json
fixmux convert examples/capture.har -t wiremock -o examples/wiremock-stubs.json
```

Try converting between any pair and proving nothing was lost:

```bash
fixmux convert examples/cassette.yml -t vcr        # Ruby cassette -> vcrpy cassette
fixmux verify examples/capture.har examples/nock-definitions.json
fixmux verify examples/capture.har examples/wiremock-stubs.json   # host ignored automatically
fixmux inspect examples/capture.har
```

Notes worth noticing while you experiment:

- Converting *to* WireMock prints a note that the origin
  (`http://api.example.test`) was dropped — stub mappings are hostless by
  design. Add `--strict` to turn that data drop into exit code 2 instead;
  converting *from* WireMock takes `--base-url` to put the origin back.
- Converting *from* HAR prints a note listing the HAR-only fields
  (`time`, `timings`) that no replay format can express.
- `verify` compares exchanges semantically (header grouping, JSON body
  equality, default ports), which is why the four very different files
  above all count as equivalent.
