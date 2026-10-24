# Changelog

## 0.1.4 (2026-10-24)

- Fixed the GitHub Action: `action.yml` from 0.1.2 and 0.1.3 did not parse, so `vernialabs/spendlint@v0` failed
  before it started. The description is now quoted.
- CI now runs the Action itself on a GitHub runner, so a broken `action.yml` cannot be released again.

## 0.1.3 (2026-10-24)

- Telemetry events are sent to Vernia's collector at `t.vernia.in`. It stores only the documented counts, never IP
  addresses. `SPENDLINT_TELEMETRY_URL=none` keeps events local; all existing opt-outs still apply and CI stays off.
- Anyone who saw the earlier notice sees the updated one once, and that run sends nothing.
- The collector's source is in `collector/`.

## 0.1.2 (2026-10-24)

- New README and package description, with links to vernia.in.
- Clearer telemetry section: what is recorded, what never is, and how to turn it off.

No changes to rules or behaviour.

## 0.1.1 (2026-10-24)

- Moved to the vernialabs organization: package links now point to github.com/vernialabs/spendlint.
- The Action reference is now `vernialabs/spendlint@v0`.
- README quick start installs from PyPI.

No changes to rules or behaviour.

## 0.1.0 (2026-10-24)

First public version.

- 10 rules for LLM cost defects in Python and TypeScript/JavaScript.
- CLI: text, JSONL, SARIF and GitHub annotation output; `--diff` to report only added lines; `--fail-on`;
  `.spendlint.yml` config; inline `spendlint: ignore` comments; Markdown step summary.
- Composite GitHub Action with pull-request diff mode, annotations, job summary and optional SARIF upload.
- Local-first, opt-out usage telemetry that never records code, paths or repository names.
