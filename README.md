# spendlint

Static checks for the code patterns that quietly inflate LLM bills in agent code: missing prompt caching,
system prompts that change on every call, unbounded agent loops and retries, cost accounting that ignores
cached tokens, and prompt bloat. Python and TypeScript/JavaScript. Pure Python, standard library only.

On a pull request it reports only the lines the pull request adds, as inline annotations, with a summary table
and optional SARIF for code scanning. Your code never leaves the runner.

> **Status: 0.1.0, early.** Tested locally by replaying the Action's steps against a real public repository.
> Not yet verified on GitHub-hosted runners.

## Quick start: GitHub Action

```yaml
name: spendlint
on: pull_request
permissions:
  contents: read
jobs:
  spendlint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1  # v7.0.1
      - uses: vernialabs/spendlint@v0
```

Every input (rules, confidence threshold, `fail-on`, diff mode, SARIF upload, telemetry) is described in
[docs/ACTION.md](docs/ACTION.md). A complete example is in
[examples/.github/workflows/spendlint.yml](examples/.github/workflows/spendlint.yml).

## Quick start: CLI

```bash
pip install spendlint
spendlint .                                    # scan a checkout
spendlint . --diff pr.patch --fail-on warning  # only lines a diff adds; exit 1 on warnings
spendlint . --format sarif --out spendlint.sarif
```

Exit codes, the `.spendlint.yml` config file and inline `# spendlint: ignore R-XXX-000 reason` comments are in
[docs/USAGE.md](docs/USAGE.md).

## What it checks

| Rule | What it flags | Severity | Real-world fixes behind it |
|---|---|---|---|
| `R-ACCT-001` | Cost computed from token counts that ignore cached-token fields | warning | 38 |
| `R-CACHE-001` | Anthropic requests with no prompt caching anywhere in the repo | warning | 11 |
| `R-CACHE-003` | Volatile value (timestamp, uuid, cwd) inside the system prompt | warning | 13 |
| `R-LOOP-001` | Agent loop around an LLM call with no iteration/step bound | warning | 3 |
| `R-LOOP-002` | Retry on an LLM call with no stop condition | warning | 2 |
| `R-CTX-008` | Indented JSON serialized into a prompt | info | 3 |
| `R-CALL-004` | Embedding requested one item at a time inside a loop | info | 5 |
| `R-MEDIA-001` | Image detail hard-coded to high | info | 0 |
| `R-OUT-001` | Chat completion with no output token cap | info | 4 |
| `R-MODEL-001` | Frontier model hard-coded for a cheap sub-task | info | 1 |

The last column counts reviewed fixes in open-source agent projects that the rule is based on.

## Limits, stated plainly

These are pattern rules, so they see code shape, not dollars or runtime behaviour. In our evaluation the rules
caught about 1 in 5 of the real defects their categories cover, and about 7 in 10 findings on repositories they
were never tuned on were real patterns. Many costly bugs (cumulative streaming usage, a cap sent under a parameter
name the provider ignores) need runtime data and are out of reach for static checks. Treat findings as review
prompts, not verdicts.

## Telemetry

spendlint records coarse usage counts (version, runtime, file counts by language, finding counts per rule) to a
local file you can read with `spendlint telemetry show`. It never records code, file names, paths or repository
names. It is off in CI unless you turn it on, and nothing is sent anywhere unless `SPENDLINT_TELEMETRY_URL` is set.
Turn it off with `spendlint telemetry off`, `SPENDLINT_TELEMETRY=off` or `DO_NOT_TRACK=1`. Full schema:
[docs/telemetry.md](docs/telemetry.md).

## License

spendlint is **source-available**, not open source. It is licensed under the
[PolyForm Small Business License 1.0.0](LICENSE): free for individuals, and for companies with fewer than 100
people and under US$1 million in annual revenue (2019 dollars; the license has the exact terms). Larger
organisations need a commercial license; see [COMMERCIAL.md](COMMERCIAL.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Run the tests with:

```bash
python -m spendlint.test_rules && python -m spendlint.test_cli && python -m spendlint.test_telemetry
```
