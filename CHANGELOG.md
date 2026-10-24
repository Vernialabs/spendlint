# Changelog

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
