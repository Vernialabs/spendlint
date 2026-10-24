"""spendlint engine: walk a checkout, run rules, optionally keep only findings on lines added by a diff."""
import ast
import fnmatch
import json
import os
import re
from pathlib import Path

from . import __version__
from .meta import RULES
from .rules_py import PY_RULES, is_llm_call, dotted, docstring_lines
from .rules_ts import TS_RULES, t_media_001

SKIP_DIR = re.compile(r"(^|/)(\.git|node_modules|venv|\.venv|dist|build|__pycache__|tests?|__tests__|docs?|examples?|cookbooks?|notebooks?|benchmarks?|e2e|fixtures?|migrations|site-packages|vendor|third_party)(/|$)", re.I)
SKIP_FILE = re.compile(r"(^test_.*\.py$|.*_test\.py$|.*\.(test|spec)\.[jt]sx?$|.*\.d\.ts$|.*\.min\.js$|conftest\.py$)")
PY_EXT = (".py",)
TS_EXT = (".ts", ".tsx", ".js", ".jsx", ".mjs")
MAX_BYTES = 400_000
# `# spendlint: ignore R-OUT-001 reason` (or `// spendlint: ignore R-OUT-001, R-CACHE-003 reason` in TS/JS)
SUPPRESS_RX = re.compile(r"(?:#|//|/\*)\s*spendlint:\s*ignore\b(.*)", re.I)
RULE_ID_RX = re.compile(r"R-[A-Z]+-\d{3}")


def iter_files(root):
    root = Path(root)
    for dp, dns, fns in os.walk(root):
        rel = Path(dp).relative_to(root).as_posix()
        dns[:] = [d for d in dns if not SKIP_DIR.search((rel + "/" + d).lstrip("./"))]
        for fn in fns:
            if SKIP_FILE.match(fn):
                continue
            if fn.endswith(PY_EXT + TS_EXT):
                p = Path(dp) / fn
                try:
                    if p.stat().st_size <= MAX_BYTES:
                        yield p, p.relative_to(root).as_posix()
                except OSError:
                    pass


def snippet(text, line, end, pad=3, cap=14):
    ls = text.splitlines()
    a, b = max(0, line - 1 - pad), min(len(ls), min(end, line + cap) + pad)
    return "\n".join(f"{i + 1:>5}  {ls[i]}" for i in range(a, b))


def path_ignored(rel, patterns):
    """`patterns` are globs from the config (`legacy/*`, `*.gen.py`); a pattern ending in `/` matches a directory prefix."""
    for pat in patterns or ():
        pat = pat.strip()
        if pat.startswith("./"):
            pat = pat[2:]
        if not pat:
            continue
        if pat.endswith("/") and (rel + "/").startswith(pat):
            return True
        if fnmatch.fnmatchcase(rel, pat) or fnmatch.fnmatchcase(rel, pat.rstrip("/") + "/*"):
            return True
    return False


def suppressed_rules(line_text):
    """Rule ids named by an inline `spendlint: ignore` comment on this line (empty set if none)."""
    m = SUPPRESS_RX.search(line_text)
    if not m:
        return set()
    ids = set()
    for tok in re.split(r"[\s,]+", m.group(1).strip()):
        if RULE_ID_RX.fullmatch(tok):
            ids.add(tok)
        elif tok:
            break  # the rest is the free-text reason
    return ids


def is_suppressed(lines, rule_id, line):
    """A finding is suppressed by a comment naming its rule on its first line, or on a comment-only line just above."""
    if 0 < line <= len(lines) and rule_id in suppressed_rules(lines[line - 1]):
        return True
    if 1 < line <= len(lines) + 1:
        above = lines[line - 2].lstrip()
        if above.startswith(("#", "//", "/*", "*")) and rule_id in suppressed_rules(above):
            return True
    return False


def scan_repo(root, rules=None, diff=None, ignore_paths=None, suppress=True):
    """Return (findings, stats). `diff` maps path -> set(added line numbers) to keep only PR-added lines.
    `ignore_paths` are config globs; `suppress` honours inline `spendlint: ignore R-XXX-000` comments."""
    findings, stats = [], {"files": 0, "py": 0, "ts": 0, "py_parse_errors": 0, "suppressed": 0, "ignored_files": 0}
    anthropic_sites, cache_control_seen = [], False
    texts = {}
    for p, rel in iter_files(root):
        if diff is not None and rel not in diff:
            continue
        if path_ignored(rel, ignore_paths):
            stats["ignored_files"] += 1
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        stats["files"] += 1
        is_py = rel.endswith(PY_EXT)
        stats["py" if is_py else "ts"] += 1
        if "cache_control" in text or "cachePoint" in text or "cache_point" in text or "anthropic_cache" in text:
            cache_control_seen = True
        raw = []
        if is_py:
            try:
                tree = ast.parse(text)
            except (SyntaxError, ValueError, RecursionError):
                stats["py_parse_errors"] += 1
                tree = None
            if tree is not None:
                for rule in PY_RULES:
                    try:
                        raw.extend(rule(tree, text, rel))
                    except Exception:
                        pass
                raw.extend(t_media_001(text, rel, docstring_lines(tree)))
                if re.search(r"anthropic|bedrock|claude", text, re.I):
                    for n in ast.walk(tree):
                        if not isinstance(n, ast.Call):
                            continue
                        d = dotted(n.func)
                        last = d.rsplit(".", 1)[-1]
                        if d.endswith(("messages.create", "messages.stream")) and re.search(r"anthropic", text, re.I):
                            has_sys = any(k.arg == "system" for k in n.keywords)
                            anthropic_sites.append((rel, n.lineno, has_sys, text))
                            break
                        # LangChain / Bedrock call surfaces that also take cache markers
                        if last in ("ChatAnthropic", "ChatBedrock", "ChatBedrockConverse", "ChatAnthropicVertex", "AnthropicBedrock", "AnthropicVertex") or (last in ("converse", "converse_stream") and re.search(r"claude|anthropic", text, re.I)):
                            anthropic_sites.append((rel, n.lineno, False, text))
                            break
        else:
            for rule in TS_RULES:
                try:
                    raw.extend(rule(text, rel))
                except Exception:
                    pass
            m = re.search(r"messages\.(create|stream)\s*\(", text)
            if m and re.search(r"@anthropic-ai/sdk|anthropic", text, re.I):
                anthropic_sites.append((rel, text.count("\n", 0, m.start()) + 1, "system" in text[m.start(): m.start() + 600], text))
            else:
                m = re.search(r"new (ConverseCommand|ConverseStreamCommand)\s*\(|createAnthropic\s*\(|\bbedrock\s*\(\s*[\"'][^\"']*(claude|anthropic)", text)
                if m and re.search(r"claude|anthropic", text, re.I):
                    anthropic_sites.append((rel, text.count("\n", 0, m.start()) + 1, False, text))
        for rid, line, end, detail, conf in raw:
            if rules is not None and rid not in rules:
                continue
            findings.append(dict(rule_id=rid, path=rel, line=line, end_line=end, detail=detail, confidence=conf, snippet=snippet(text, line, end)))
            texts[rel] = text  # kept only for files with findings, for the suppression check
    # repo-level: anthropic without any cache_control
    if (rules is None or "R-CACHE-001" in rules) and anthropic_sites and not cache_control_seen:
        sites = sorted(anthropic_sites, key=lambda s: (not s[2], s[0]))
        rel, line, has_sys, text = sites[0]
        texts[rel] = text
        findings.append(dict(rule_id="R-CACHE-001", path=rel, line=line, end_line=line,
                             detail=f"{len(anthropic_sites)} file(s) call Anthropic messages API; `cache_control` appears nowhere in the repo",
                             confidence=0.6 if has_sys else 0.45, snippet=snippet(text, line, line)))
    if diff is not None:
        findings = [f for f in findings if any(l in diff.get(f["path"], ()) for l in range(f["line"], f["end_line"] + 1))]
    if suppress:
        kept = []
        for f in findings:
            if is_suppressed(texts.get(f["path"], "").splitlines(), f["rule_id"], f["line"]):
                stats["suppressed"] += 1
            else:
                kept.append(f)
        findings = kept
    for f in findings:
        meta = RULES[f["rule_id"]]
        f.update(rule_name=meta["name"], severity=meta["severity"], category=meta["category"], fp_risk=meta["fp_risk"],
                 dataset_evidence=meta["evidence"], recommended_action=meta["action"])
    findings.sort(key=lambda f: (-f["confidence"], f["rule_id"], f["path"], f["line"]))
    return findings, stats


def parse_diff(diff_text):
    """unified diff -> {path: {added line numbers in the new file}}"""
    out, path, new = {}, None, 0
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            path = line[4:].strip()
            path = path[2:] if path.startswith("b/") else path
            out.setdefault(path, set())
        elif line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            new = int(m.group(1)) if m else 0
        elif path and line.startswith("+") and not line.startswith("+++"):
            out[path].add(new)
            new += 1
        elif path and not line.startswith("-"):
            new += 1
    return out


def to_sarif(findings, tool_version=__version__):
    rules = [{"id": rid, "name": m["name"], "shortDescription": {"text": m["name"]}, "help": {"text": m["action"]},
              "properties": {"category": m["category"], "falsePositiveRisk": m["fp_risk"], "datasetEvidence": m["evidence"]}}
             for rid, m in RULES.items()]
    results = [{"ruleId": f["rule_id"], "level": {"warning": "warning", "info": "note"}.get(f["severity"], "note"),
                "message": {"text": f["detail"] + " - " + f["recommended_action"]},
                "locations": [{"physicalLocation": {"artifactLocation": {"uri": f["path"]},
                                                    "region": {"startLine": f["line"], "endLine": f["end_line"]}}}]} for f in findings]
    return {"version": "2.1.0", "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
            "runs": [{"tool": {"driver": {"name": "spendlint", "version": tool_version, "rules": rules}}, "results": results}]}
