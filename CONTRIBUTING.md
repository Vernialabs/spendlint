# Contributing

Thanks for your interest. Issues are very welcome, especially:

- **False positives**: a finding on code that is fine. Include the smallest snippet that reproduces it.
- **Missed defects**: a real cost bug spendlint did not flag, ideally with a link to the public fix.
- **Rule ideas** backed by a real-world example.

**Pull requests:** spendlint is offered under a source-available license with commercial licenses for larger
organisations, so code contributions need a contributor license agreement before they can be merged. Until that is
in place, please open an issue first rather than a pull request.

## Development

Pure Python, standard library only, Python 3.9 or newer.

```bash
python -m spendlint.test_rules     # rule cases
python -m spendlint.test_cli       # CLI, config, suppression, output formats
python -m spendlint.test_telemetry # telemetry never records code, paths or repo names
```

Each rule needs positive and negative cases in `spendlint/test_rules.py`. A rule should keep false positives rare
on pull-request diffs: a noisy rule is worse than a missing one.

`scripts/action_local/replay_action.sh` replays the GitHub Action's steps locally against a clone with a pull
request branch checked out.

## AI review

Every pull request gets an advisory AI review, posted as one summary comment plus inline comments; it never
approves or blocks. `.github/workflows/pr-review.yml` calls the provider-neutral `.github/workflows/ai-review.yml`;
the prompt, output schema and posting script live in `.github/ai-review/`. Switch provider on the `provider:` line
of `pr-review.yml` (`claude` or `codex`) or with the repository variable `AI_REVIEW_PROVIDER`. Label a pull request
`ai-review-skip` to skip it, or `ai-rereview` to run it again. Without the provider's secret the review is skipped
with a notice. Test the posting script with `python -m unittest discover -s tests/ai_review`, or preview a review
with `python .github/ai-review/post_review.py --review review.json --diff pr.diff --dry-run`.
