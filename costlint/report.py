"""Output formats (text, jsonl, sarif, github workflow commands), the Markdown step summary, and exit codes."""
import json

from .meta import RULES
from .scan import to_sarif

EXIT_CLEAN, EXIT_FINDINGS, EXIT_ERROR = 0, 1, 2
SEVERITY_RANK = {"info": 1, "warning": 2}
FAIL_ON = ("none", "info", "warning")
SUMMARY_MAX_ROWS = 50


def exit_code(findings, fail_on):
    """0 when nothing is at or above the `fail_on` severity, 1 otherwise. `none` never fails on findings."""
    if fail_on == "none":
        return EXIT_CLEAN
    floor = SEVERITY_RANK[fail_on]
    return EXIT_FINDINGS if any(SEVERITY_RANK.get(f["severity"], 0) >= floor for f in findings) else EXIT_CLEAN


def format_text(findings, stats):
    lines = [f"{f['path']}:{f['line']}  {f['rule_id']}  [{f['severity']}, conf {f['confidence']}]  {f['detail']}" for f in findings]
    lines.append(f"-- {len(findings)} finding(s); scanned {stats['files']} files ({stats['py']} py, {stats['ts']} ts)"
                 + (f"; {stats['suppressed']} suppressed inline" if stats.get("suppressed") else ""))
    return "\n".join(lines)


def format_jsonl(findings):
    return "\n".join(json.dumps(f, ensure_ascii=False) for f in findings)


def format_sarif(findings):
    return json.dumps(to_sarif(findings), indent=1)


def _escape_data(s):
    return str(s).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _escape_prop(s):
    return _escape_data(s).replace(":", "%3A").replace(",", "%2C")


def format_github(findings):
    """GitHub Actions workflow commands: one `::warning` / `::notice` annotation per finding."""
    out = []
    for f in findings:
        level = "warning" if f["severity"] == "warning" else "notice"
        props = ",".join([f"file={_escape_prop(f['path'])}", f"line={f['line']}", f"endLine={max(f['end_line'], f['line'])}",
                          f"title={_escape_prop(f['rule_id'] + ' ' + f['rule_name'])}"])
        msg = f"{f['detail']} (confidence {f['confidence']}). {f['recommended_action']}"
        out.append(f"::{level} {props}::{_escape_data(msg)}")
    return "\n".join(out)


def _cell(s):
    return str(s).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def summary_markdown(findings, stats, title="costlint", diff_note=None):
    """A Markdown block for $GITHUB_STEP_SUMMARY: totals, per-rule counts and the first findings."""
    md = [f"## {title}", ""]
    scanned = f"Scanned {stats['files']} file(s) ({stats['py']} Python, {stats['ts']} TypeScript/JavaScript)"
    if diff_note:
        scanned += f"; {diff_note}"
    md.append(scanned + ".")
    if stats.get("suppressed"):
        md.append(f"{stats['suppressed']} finding(s) suppressed by inline `costlint: ignore` comments.")
    md.append("")
    if not findings:
        md += ["No cost findings.", ""]
        return "\n".join(md)
    warn = sum(f["severity"] == "warning" for f in findings)
    md += [f"**{len(findings)} finding(s)**: {warn} warning, {len(findings) - warn} info.", ""]
    md += ["| Rule | Name | Severity | Count |", "|---|---|---|---|"]
    counts = {}
    for f in findings:
        counts[f["rule_id"]] = counts.get(f["rule_id"], 0) + 1
    for rid, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        md.append(f"| {rid} | {_cell(RULES[rid]['name'])} | {RULES[rid]['severity']} | {n} |")
    md += ["", "| Severity | Rule | Location | Confidence | Detail |", "|---|---|---|---|---|"]
    for f in findings[:SUMMARY_MAX_ROWS]:
        md.append(f"| {f['severity']} | {f['rule_id']} | `{_cell(f['path'])}:{f['line']}` | {f['confidence']} | {_cell(f['detail'])} |")
    if len(findings) > SUMMARY_MAX_ROWS:
        md.append(f"\n_{len(findings) - SUMMARY_MAX_ROWS} more finding(s) not shown; see the SARIF file or annotations._")
    md.append("")
    return "\n".join(md)
