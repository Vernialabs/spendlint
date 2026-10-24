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
