# Contributing

Thanks for your interest. Issues are very welcome, especially:

- **False positives**: a finding on code that is fine. Include the smallest snippet that reproduces it.
- **Missed defects**: a real cost bug spendlint did not flag, ideally with a link to the public fix.
- **Rule ideas** backed by a real-world example.

**Pull requests:** spendlint is offered under a source-available license with commercial licenses for larger
organisations, so code contributions need a contributor license agreement before they can be merged. Until that is
in place, please open an issue first rather than a pull request.

## Development

Python 3.11 or newer. Development tools are pinned in `uv.lock`; install [uv](https://docs.astral.sh/uv/) and run:

```bash
uv sync --group dev          # pytest, ruff, pyright, pre-commit, build, twine
uv run pre-commit install    # ruff, pyright, actionlint and zizmor on every commit
uv run pytest                # rule cases, CLI, config, suppression, formats, telemetry
uv run ruff check . && uv run ruff format --check . && uv run pyright
```

Each rule needs positive and negative cases in `tests/test_rules.py`. A rule should keep false positives rare
on pull-request diffs: a noisy rule is worse than a missing one.

`scripts/action_local/replay_action.sh` replays the GitHub Action's steps locally against a clone with a pull
request branch checked out.
