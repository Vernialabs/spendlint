# spendlint

Static checks for cost defects in LLM agent code: missing prompt caching, volatile values in system prompts,
unbounded agent loops and retries, indented JSON in prompts, one-at-a-time embeddings, uncapped output, and
mis-counted cached tokens. Python (via `ast`) and TypeScript/JavaScript (via patterns). Pure Python, standard
library only.

The rules come from a reviewed study of 185 real cost-defect fixes in open-source agent projects. Measured quality,
stated plainly: the rules catch about 1 in 5 of the real defects their categories cover, and about 7 in 10 findings
on repositories they were never tuned on were real patterns. Treat findings as review prompts, not verdicts.

## Install

From a checkout (there is no PyPI package yet):

```bash
pip install .            # provides the `spendlint` command
pip install ".[yaml]"    # optional: full YAML support for .spendlint.yml via PyYAML
spendlint --version
```

Python 3.9 or newer. `python -m spendlint ...` works the same as `spendlint ...`.

## Scan

```bash
spendlint .                                     # text report
spendlint . --format jsonl                      # one JSON object per finding
spendlint . --format sarif --out spendlint.sarif
spendlint . --format github                     # GitHub workflow commands (::warning file=...,line=...::...)
spendlint . --diff-base origin/main             # only lines added since the merge base with origin/main
spendlint . --diff pr.patch                     # only lines added by a unified diff
spendlint . --sarif out.sarif --summary-md "$GITHUB_STEP_SUMMARY"   # extra outputs alongside the main one
spendlint . --rules R-CACHE-003,R-CTX-008 --min-confidence 0.5
spendlint . --fail-on warning                   # exit 1 if any warning-level finding remains
```

### Exit codes

| Code | Meaning |
|---|---|
| 0 | no findings at or above `--fail-on` (default `none`: findings never fail) |
| 1 | at least one finding at or above `--fail-on` (`info` counts info and warning; `warning` counts warning only) |
| 2 | usage error, bad config, missing path, git failure, or internal error |

### Config file

spendlint looks for `.spendlint.yml`, `.spendlint.yaml` or `.spendlint.json` in the scanned directory
(`--config FILE` to point elsewhere, `--no-config` to ignore it). Command-line flags win over the file.

```yaml
disable: [R-OUT-001, R-MODEL-001]   # rules to turn off
ignore_paths:                       # globs relative to the scanned directory; a trailing / means a directory
  - legacy/*
  - generated/
min_confidence: 0.5
```

Without PyYAML, a built-in reader handles exactly this flat shape (scalars, `[a, b]` lists, `- item` lists).
Anything more complex needs PyYAML or the JSON form. Unknown keys or rule ids are an error (exit 2), so typos do not
silently disable nothing.

### Inline suppression

```python
SYSTEM_PROMPT = f"Today: {now}"  # spendlint: ignore R-CACHE-003 rounded to the day upstream

# spendlint: ignore R-OUT-001, R-MODEL-001 capped by the gateway
resp = client.chat.completions.create(model="gpt-4o", messages=m)
```

```ts
// spendlint: ignore R-CACHE-003 cache disabled for this call
const systemPrompt = `Now: ${new Date().toISOString()}`;
```

The comment must name the rule id(s); a bare `spendlint: ignore` suppresses nothing. It applies on the finding's first
line or on a comment-only line directly above it. Text after the ids is the reason. Suppressed findings are counted in
the text report and the step summary. Repository-level findings (R-CACHE-001) are turned off with `disable` instead.

## Rules

| Rule | Severity | What it flags |
|---|---|---|
| R-ACCT-001 | warning | cost or usage computed from token counts that ignore cached-token fields |
| R-CACHE-001 | warning | Anthropic calls with no `cache_control` anywhere in the repo |
| R-CACHE-003 | warning | timestamp, uuid, working directory or session id at the head of a system prompt or cache key |
| R-LOOP-001 | warning | `while True` agent loop around a model call with no step or budget bound |
| R-LOOP-002 | warning | retry decorator on a model call with no stop condition |
| R-CTX-008 | info | `json.dumps(..., indent=N)` (or message objects) serialized into a prompt |
| R-CALL-004 | info | embeddings requested one item at a time in a loop |
| R-MEDIA-001 | info | image `detail: high` hard-coded |
| R-OUT-001 | info | chat completion with no output token cap |
| R-MODEL-001 | info | frontier model hard-coded for a title/summary/classification helper |

## GitHub Action

See `docs/ACTION.md`.

## Telemetry

Local CLI runs record coarse, anonymous usage counts to a local log after a one-time notice; CI is off unless you opt
in; nothing is sent anywhere unless `SPENDLINT_TELEMETRY_URL` is set. No code, paths or names are ever recorded.
`spendlint telemetry status|show|off|on`, `SPENDLINT_TELEMETRY=off` or `DO_NOT_TRACK=1`. Full schema: `docs/telemetry.md`.

## Tests

```bash
python -m spendlint.test_rules                                   # 63 rule cases
python -m unittest spendlint.test_cli spendlint.test_telemetry    # CLI, config, action, telemetry
```
