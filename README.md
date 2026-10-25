# spendlint

**Catch the code that inflates your LLM bill, before it ships.**

spendlint reads your agent code and points at the patterns that quietly multiply token spend: prompts that never
hit the cache, system prompts that change on every call, agent loops with no limit, retries that never stop, and
cost reports that miss cached tokens. It works on Python and TypeScript/JavaScript, installs in seconds, and has no
dependencies.

On a pull request it only speaks up about the lines that pull request adds, right where you review them. Your code
never leaves your machine or your CI runner.

Built by [Vernia](https://vernia.in). Free while we are in early access.

## Get started

### In GitHub Actions

Add `.github/workflows/spendlint.yml`:

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

Findings show up as annotations on the changed lines, with a summary on the run page. Every option (rule
selection, confidence threshold, failing the build, SARIF for code scanning) is in
[docs/ACTION.md](https://github.com/vernialabs/spendlint/blob/main/docs/ACTION.md), and there is a complete
[example workflow](https://github.com/vernialabs/spendlint/blob/main/examples/.github/workflows/spendlint.yml).

### On your machine

```bash
pip install spendlint
spendlint .                                    # scan a project
spendlint . --diff pr.patch --fail-on warning  # only lines a diff adds; exit 1 on warnings
spendlint . --format sarif --out spendlint.sarif
```

Configuration, exit codes and inline ignores are in
[docs/USAGE.md](https://github.com/vernialabs/spendlint/blob/main/docs/USAGE.md).

## What it catches

| Rule | What it flags | Severity |
|---|---|---|
| `R-ACCT-001` | Cost computed from token counts that ignore cached-token fields | warning |
| `R-CACHE-001` | Anthropic requests with no prompt caching anywhere in the repo | warning |
| `R-CACHE-003` | A timestamp, uuid or path inside the system prompt, which breaks the cache | warning |
| `R-LOOP-001` | Agent loop around an LLM call with no step limit | warning |
| `R-LOOP-002` | Retry on an LLM call with no stop condition | warning |
| `R-CTX-008` | Pretty-printed JSON sent in a prompt | info |
| `R-CALL-004` | Embeddings requested one item at a time inside a loop | info |
| `R-MEDIA-001` | Image detail hard-coded to high | info |
| `R-OUT-001` | Chat completion with no output token cap | info |
| `R-MODEL-001` | Frontier model hard-coded for a cheap sub-task | info |

Every rule comes from a real cost bug that an open-source agent team found and fixed. spendlint reads code, not
invoices, so treat each finding as a pointer worth a second look in review.

## What's next

spendlint is the first product from Vernia. Next up are **Vernia Reason**, a reasoning harness that reviews pull
requests with the context of the whole repository, and **Vernia Ledger**, an agent that connects to your providers
and billing to tie every dollar to a feature and a customer. Want early access? Write to
[ishan.upadhyay.iitb@gmail.com](mailto:ishan.upadhyay.iitb@gmail.com).

## Telemetry

spendlint keeps anonymous usage counts so we can see which rules help and where they get in the way. Here is
exactly what that means:

- **What is recorded:** spendlint version, Python version, operating system, CI provider, how many files were
  scanned per language, how many findings each rule produced, run time, exit code, the hour of the run, and a random
  install id.
- **What is never recorded:** your code, file names, paths, repository names, or anything that identifies you.
- **Where it goes:** a local file you can read at any time with `spendlint telemetry show`, and Vernia's collector
  at `t.vernia.in`, which stores only these counts (not your IP address). Its source is in
  [collector/](https://github.com/vernialabs/spendlint/tree/main/collector). Set `SPENDLINT_TELEMETRY_URL=none`
  to keep everything local.
- **In CI:** off by default. Turn it on with `SPENDLINT_TELEMETRY=on` or the Action's `telemetry: on` input.
- **Turning it off:** `spendlint telemetry off`, `SPENDLINT_TELEMETRY=off`, or `DO_NOT_TRACK=1`.
- **First run:** spendlint prints this list once before it records anything.

The full schema is in [docs/telemetry.md](https://github.com/vernialabs/spendlint/blob/main/docs/telemetry.md).
A paid plan with telemetry fully disabled is on the way for teams that need it.

## License

spendlint is source-available under the
[PolyForm Small Business License 1.0.0](https://github.com/vernialabs/spendlint/blob/main/LICENSE). It is free
for individuals and for companies with fewer than 100 people and under US$1 million in annual revenue (the
license has the exact terms). Larger organisations need a commercial license; see
[COMMERCIAL.md](https://github.com/vernialabs/spendlint/blob/main/COMMERCIAL.md).

## Contributing

See [CONTRIBUTING.md](https://github.com/vernialabs/spendlint/blob/main/CONTRIBUTING.md). Run the tests with:

```bash
uv sync --group dev
uv run pytest
```
