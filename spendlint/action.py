"""`spendlint action`: what the composite GitHub Action (action.yml) runs.

Inputs arrive as environment variables set by action.yml (composite actions do not export INPUT_* on
their own, so the step maps each input explicitly):

    INPUT_PATH            directory to scan, relative to GITHUB_WORKSPACE (default .)
    INPUT_RULES           comma-separated rule ids (default all)
    INPUT_MIN_CONFIDENCE  0-1 (default: config file, else 0)
    INPUT_FAIL_ON         none|info|warning (default none)
    INPUT_DIFF_MODE       auto|on|off (default auto: on for pull_request events)
    INPUT_DIFF_BASE       revision to compare with (default origin/$GITHUB_BASE_REF)
    INPUT_SARIF_OUTPUT    where to write SARIF (default $RUNNER_TEMP/spendlint.sarif)
    INPUT_CONFIG          config file (default: .spendlint.yml/.yaml/.json in the scanned directory)
    INPUT_TELEMETRY       auto|on|off (default auto: off in CI unless SPENDLINT_TELEMETRY=on)

It prints one annotation per finding, appends a summary to $GITHUB_STEP_SUMMARY, writes `findings-count`
and `sarif-path` to $GITHUB_OUTPUT, and exits 0 / 1 / 2 like the CLI (action/scan.sh records that as `exit-code`).
"""

import os
import sys
import tempfile
from pathlib import Path

from . import telemetry
from .cli import UsageError, parse_rules, run_scan, write_text
from .config import ConfigError
from .gitdiff import GitError, added_lines
from .report import EXIT_ERROR, FAIL_ON, _escape_data, exit_code, format_github, format_sarif, summary_markdown

PR_EVENTS = ("pull_request", "pull_request_target")


def _input(env, name, default=""):
    v = env.get("INPUT_" + name, "").strip()
    return v if v else default


def _set_outputs(env, **values):
    path = env.get("GITHUB_OUTPUT")
    lines = "".join(f"{k}={v}\n" for k, v in values.items())
    if path:
        with open(path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(lines)


def main(argv=None, env=None):
    env = os.environ if env is None else env
    timer = telemetry.Timer()
    findings, stats, code = [], {}, EXIT_ERROR
    tel = _input(env, "TELEMETRY", "auto").lower()
    if tel in ("on", "off"):
        os.environ["SPENDLINT_TELEMETRY"] = tel  # the explicit Action input wins over the inherited environment
    try:
        workspace = Path(env.get("GITHUB_WORKSPACE") or os.getcwd())
        root = (workspace / _input(env, "PATH", ".")).resolve()
        fail_on = _input(env, "FAIL_ON", "none").lower()
        if fail_on not in FAIL_ON:
            raise UsageError(f"fail-on must be one of {', '.join(FAIL_ON)}, got {fail_on!r}")
        mode = _input(env, "DIFF_MODE", "auto").lower()
        if mode not in ("auto", "on", "off"):
            raise UsageError(f"diff-mode must be auto, on or off, got {mode!r}")
        mc = _input(env, "MIN_CONFIDENCE")
        try:
            min_conf = float(mc) if mc else None
        except ValueError:
            raise UsageError(f"min-confidence must be a number, got {mc!r}") from None
        if min_conf is not None and not 0 <= min_conf <= 1:
            raise UsageError("min-confidence must be between 0 and 1")
        rules = parse_rules(_input(env, "RULES"))
        config = _input(env, "CONFIG")
        config = str(workspace / config) if config else None
        sarif_path = Path(
            _input(env, "SARIF_OUTPUT") or Path(env.get("RUNNER_TEMP") or tempfile.gettempdir()) / "spendlint.sarif"
        )
        if not sarif_path.is_absolute():
            sarif_path = workspace / sarif_path

        event = env.get("GITHUB_EVENT_NAME", "")
        base_ref = env.get("GITHUB_BASE_REF", "")
        base = _input(env, "DIFF_BASE") or (f"origin/{base_ref}" if base_ref else "")
        use_diff = mode == "on" or (mode == "auto" and event in PR_EVENTS and bool(base))
        diff, note = None, "full scan"
        if use_diff:
            try:
                if not base:
                    raise GitError(
                        "diff-mode is on but there is no base: set diff-base, or run on a pull_request event"
                    )
                diff, note = added_lines(root, base, merge_commit=(event == "pull_request"), fetch=True)
            except GitError as e:
                if mode == "on":
                    raise
                print(
                    f"::warning title=spendlint::Could not compute the pull request diff ({e}); scanning all files instead."
                )
                note = "full scan (pull request diff unavailable)"
        try:  # annotations and SARIF need paths relative to the workspace (the repository root)
            prefix = root.relative_to(workspace.resolve()).as_posix() + "/"
        except ValueError:
            prefix = ""
        prefix = "" if prefix == "./" else prefix

        findings, stats, config_used = run_scan(root, rules, min_conf, config, True, diff)
        for f in findings:
            f["path"] = prefix + f["path"]

        print(
            f"spendlint: {len(findings)} finding(s) in {stats['files']} file(s); {note}"
            + (f"; config {Path(config_used).name}" if config_used else "")
        )
        annotations = format_github(findings)
        if annotations:
            print(annotations)
        sarif_path.parent.mkdir(parents=True, exist_ok=True)
        write_text(sarif_path, format_sarif(findings))
        if env.get("GITHUB_STEP_SUMMARY"):
            write_text(env["GITHUB_STEP_SUMMARY"], summary_markdown(findings, stats, diff_note=note), append=True)
        code = exit_code(findings, fail_on)
        _set_outputs(env, **{"findings-count": len(findings), "sarif-path": sarif_path.as_posix()})
    except (UsageError, ConfigError, GitError, OSError) as e:
        print(f"::error title=spendlint::{_escape_data(e)}")
        code = EXIT_ERROR
    sys.stdout.flush()
    telemetry.record("action", timer.elapsed(), stats, findings, code)
    return code
