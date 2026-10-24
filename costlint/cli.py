"""Command line.

    costlint [scan] PATH [--rules R-A,R-B] [--min-confidence 0.5] [--config FILE | --no-config]
                         [--diff pr.patch | --diff-base origin/main]
                         [--format text|jsonl|sarif|github] [--out FILE] [--sarif FILE] [--summary-md FILE]
                         [--fail-on none|info|warning]
    costlint action                   run inside the GitHub Action (reads INPUT_* environment variables)
    costlint telemetry status|show|on|off
    costlint --version

Exit codes: 0 no findings at or above --fail-on, 1 findings at or above it, 2 usage or internal error.
"""
import argparse
import sys
from pathlib import Path

from . import __version__, telemetry
from .config import ConfigError, find_config, load_config
from .gitdiff import GitError, added_lines
from .meta import RULES
from .report import EXIT_ERROR, FAIL_ON, exit_code, format_github, format_jsonl, format_sarif, format_text, summary_markdown
from .scan import parse_diff, scan_repo


class UsageError(ValueError):
    pass


def parse_rules(value):
    if not value or not value.strip():
        return None
    ids = [r.strip() for r in value.replace(" ", ",").split(",") if r.strip()]
    bad = [r for r in ids if r not in RULES]
    if bad:
        raise UsageError(f"unknown rule id(s): {', '.join(bad)}; known: {', '.join(sorted(RULES))}")
    return set(ids)


def run_scan(root, rules=None, min_confidence=None, config_path=None, use_config=True, diff=None):
    """Resolve config + flags, scan, filter. Flags win over the config file. Returns (findings, stats, config_used)."""
    root = Path(root)
    if not root.exists():
        raise UsageError(f"path does not exist: {root}")
    cfg = {"disable": set(), "ignore_paths": [], "min_confidence": None}
    if config_path:
        cfg = load_config(config_path)
    elif use_config:
        found = find_config(root)
        if found:
            cfg, config_path = load_config(found), found
    selected = set(rules) if rules is not None else set(RULES)
    selected -= cfg["disable"]
    findings, stats = scan_repo(root, selected, diff, ignore_paths=cfg["ignore_paths"])
    floor = min_confidence if min_confidence is not None else (cfg["min_confidence"] or 0.0)
    findings = [f for f in findings if f["confidence"] >= floor]
    return findings, stats, config_path


def build_parser():
    ap = argparse.ArgumentParser(prog="costlint", description="Static cost-defect checks for LLM agent code.",
                                 epilog="Other commands: `costlint action`, `costlint telemetry status|show|on|off`.")
    ap.add_argument("--version", action="version", version=f"costlint {__version__}")
    ap.add_argument("path", help="directory (or file) to scan")
    ap.add_argument("--rules", help="comma-separated rule ids to run (default: all)")
    ap.add_argument("--min-confidence", type=float, default=None, help="drop findings below this confidence (0-1)")
    ap.add_argument("--config", help="config file (.costlint.yml or .costlint.json); default: look in PATH")
    ap.add_argument("--no-config", action="store_true", help="ignore any config file in PATH")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--diff", help="unified diff file; only report findings on added lines")
    g.add_argument("--diff-base", metavar="REV", help="git revision (e.g. origin/main); only report lines added since it")
    ap.add_argument("--format", default="text", choices=["text", "jsonl", "sarif", "github"])
    ap.add_argument("--out", help="write the main output here instead of stdout")
    ap.add_argument("--sarif", metavar="FILE", help="also write SARIF to FILE")
    ap.add_argument("--summary-md", metavar="FILE", help="append a Markdown summary to FILE (e.g. $GITHUB_STEP_SUMMARY)")
    ap.add_argument("--fail-on", default="none", choices=FAIL_ON,
                    help="exit 1 when a finding of this severity or higher remains (default: none)")
    return ap


def render(fmt, findings, stats):
    if fmt == "jsonl":
        return format_jsonl(findings)
    if fmt == "sarif":
        return format_sarif(findings)
    if fmt == "github":
        return format_github(findings)
    return format_text(findings, stats)


def write_text(path, text, append=False):
    with open(path, "a" if append else "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text if text.endswith("\n") or not text else text + "\n")


def scan_command(argv):
    timer = telemetry.Timer()
    a = build_parser().parse_args(argv)  # exits 2 on usage errors
    findings, stats, code = [], {}, EXIT_ERROR
    try:
        if a.min_confidence is not None and not 0 <= a.min_confidence <= 1:
            raise UsageError("--min-confidence must be between 0 and 1")
        diff, note = None, None
        if a.diff:
            with open(a.diff, encoding="utf-8", errors="replace") as fh:
                diff = parse_diff(fh.read())
        elif a.diff_base:
            diff, note = added_lines(a.path, a.diff_base)
        findings, stats, _ = run_scan(a.path, parse_rules(a.rules), a.min_confidence, a.config, not a.no_config, diff)
        out = render(a.format, findings, stats)
        if a.out:
            write_text(a.out, out)
        elif out:
            sys.stdout.write(out + "\n")
        if a.sarif:
            write_text(a.sarif, format_sarif(findings))
        if a.summary_md:
            write_text(a.summary_md, summary_markdown(findings, stats, diff_note=note), append=True)
        code = exit_code(findings, a.fail_on)
    except (UsageError, ConfigError, GitError, OSError) as e:
        sys.stderr.write(f"costlint: error: {e}\n")
        code = EXIT_ERROR
    telemetry.record("scan", timer.elapsed(), stats, findings, code)
    return code


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    try:
        if argv and argv[0] == "telemetry":
            return telemetry.command(argv[1:])
        if argv and argv[0] == "action":
            from .action import main as action_main
            return action_main(argv[1:])
        if argv and argv[0] == "scan":
            argv = argv[1:]
        return scan_command(argv)
    except SystemExit as e:  # argparse: --help/--version exit 0, usage errors exit 2
        return e.code if isinstance(e.code, int) else EXIT_ERROR
    except KeyboardInterrupt:
        return 130
    except Exception as e:  # never a traceback-shaped exit 1 that looks like "findings"
        sys.stderr.write(f"costlint: internal error: {type(e).__name__}: {e}\n")
        return EXIT_ERROR
