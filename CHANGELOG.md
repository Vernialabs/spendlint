# Changelog

## 0.1.0 (unreleased)

First public version.

- 10 rules for LLM cost defects in Python and TypeScript/JavaScript.
- CLI: text, JSONL, SARIF and GitHub annotation output; `--diff` to report only added lines; `--fail-on`;
  `.spendlint.yml` config; inline `spendlint: ignore` comments; Markdown step summary.
- Composite GitHub Action with pull-request diff mode, annotations, job summary and optional SARIF upload.
- Local-first, opt-out usage telemetry that never records code, paths or repository names.
