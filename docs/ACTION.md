# spendlint GitHub Action

A composite action (no Docker). It creates a virtualenv on the runner, installs spendlint from the action's own
checkout (no PyPI package needed), scans, prints one annotation per finding, writes a Markdown table to the job
summary, and can upload SARIF to code scanning. Code never leaves the runner.

> Status: 0.1.0, early. Tested by replaying the action's steps locally against a real public repository;
> not yet verified on GitHub-hosted runners.

## Minimal workflow

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

On a pull request this reports only findings on lines the pull request adds. Findings already on the base branch
are not shown. A full example with every input is in `examples/.github/workflows/spendlint.yml`.

## Inputs

| Input | Default | Meaning |
|---|---|---|
| `path` | `.` | directory to scan, relative to the repository root |
| `rules` | all | comma-separated rule ids |
| `min-confidence` | config, else 0 | drop findings below this confidence (0-1) |
| `fail-on` | `none` | `none`, `info` or `warning`: fail the job when a finding at or above this severity remains |
| `diff-mode` | `auto` | `auto`: diff on `pull_request`/`pull_request_target` events, full scan otherwise. `on`: always diff against `diff-base`. `off`: full scan |
| `diff-base` | `origin/<base branch>` | revision to diff against |
| `config` | auto | config file relative to the repository root (default: `.spendlint.yml/.yaml/.json` in `path`) |
| `sarif-output` | `$RUNNER_TEMP/spendlint.sarif` | where to write SARIF (always written) |
| `upload-sarif` | `false` | upload SARIF with `github/codeql-action/upload-sarif` (pinned to v4.39.1 by commit SHA) |
| `telemetry` | `auto` | `auto` = off in CI unless `SPENDLINT_TELEMETRY=on`; `on`/`off` explicit. See `docs/telemetry.md` |

## Outputs

| Output | Meaning |
|---|---|
| `findings-count` | findings reported after diff, config and suppression filters |
| `sarif-path` | absolute path of the SARIF file |
| `exit-code` | 0 clean, 1 findings at or above `fail-on`, 2 usage or internal error |

## What the steps do

1. `action/install.sh`: finds Python 3.9+ (`python3`, then `python`), creates or reuses `$RUNNER_TEMP/spendlint-venv`
   (a venv avoids PEP 668 errors on Ubuntu 24.04's system Python), runs `pip install $GITHUB_ACTION_PATH`, prints
   `spendlint --version`, writes `python=<venv python>` to `$GITHUB_OUTPUT`. pip downloads the setuptools build
   backend from PyPI in its isolated build environment; spendlint itself has no runtime dependencies.
2. `action/scan.sh`: runs `spendlint action` with the inputs as `INPUT_*` environment variables (never interpolated
   into the script, so input values cannot inject shell), and records its exit code as the `exit-code` output
   without failing the step, so the SARIF upload still runs.
3. Optional `upload-sarif` step.
4. `exit $EXIT_CODE` applies `fail-on`.

### How the pull request diff is computed

`actions/checkout` on a `pull_request` event checks out the PR merge commit at depth 1. Its first parent is the base
branch tip, so spendlint fetches that one commit (`git fetch --depth=1 origin <sha>`) and diffs `HEAD^1..HEAD`: exactly
the lines the PR adds. If HEAD is not a merge commit (for example you checked out `github.event.pull_request.head.sha`),
it fetches the base branch (`--depth=100`) and diffs from the merge base, falling back to a direct diff when the history
is too shallow. With `fetch-depth: 0` nothing is fetched. The fetch uses the credentials `actions/checkout` persists
by default; if you set `persist-credentials: false`, use `fetch-depth: 0`.

If the diff cannot be computed in `auto` mode, the action emits a warning annotation and scans every file instead,
and the summary says so. With `diff-mode: on`, that is an error (exit 2).

## Annotations, summary and SARIF

- Annotations: `warning`-severity rules become `::warning`, `info` rules become `::notice`, on the finding's file
  and lines. GitHub caps how many annotations it displays per step (10 per level when this was written); the rest are in the
  job log, the summary and the SARIF.
- Summary: counts per rule and the first 50 findings, appended to `$GITHUB_STEP_SUMMARY`.
- SARIF: always written. With `upload-sarif: true` it goes to the code scanning tab (needs
  `permissions: security-events: write`; code scanning on private repositories needs GitHub Advanced Security).
  In PR diff mode the SARIF holds only the PR's new findings; for a full baseline in the Security tab, also run on
  `push` to the default branch, where `auto` scans everything.

## Config and suppression

Same as the CLI (`docs/USAGE.md`): `.spendlint.yml` with `disable`, `ignore_paths`, `min_confidence`, and inline
`# spendlint: ignore R-XXX-000 reason` comments.

## Runners

Written for `ubuntu-latest`. `windows-latest` and `macos-latest` should work (bash is available on all three and the
install script handles the Windows venv layout) but have not been run. Self-hosted runners need Python 3.9+ and git,
and network access to PyPI for pip's build backend.

## Running the steps locally

`scripts/action_local/replay_action.sh` replays the action's steps against a local clone with the same scripts and
environment variables a GitHub runner sets, without Docker or `act`.
