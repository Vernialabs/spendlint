#!/usr/bin/env python3
"""Validate an AI review (review.json) and post it to a pull request.

Provider-neutral: Claude Code, Codex or anything else only has to write JSON matching review.schema.json.

Posts, using the workflow's GITHUB_TOKEN:
  * one sticky summary comment, found again by MARKER and edited in place on every run;
  * at most MAX_FINDINGS inline comments, all inside ONE pull-request review whose event is always COMMENT.
    Only findings whose (path, line) is an added or context line of the diff are posted inline; the rest are
    listed in the summary comment.

It never approves and never requests changes.

Standard library only. Usage:
  post_review.py --review review.json --diff pr.diff --validate-only
  post_review.py --review review.json --diff pr.diff --dry-run
  post_review.py --review review.json --diff pr.diff --repo owner/name --pr 12 --sha <head sha>
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

MARKER = "<!-- spendlint-ai-review -->"
MAX_FINDINGS = 10
SEVERITIES = ("blocker", "major", "minor", "nit")
VERDICTS = ("looks_good", "needs_changes")
FINDING_KEYS = ("path", "line", "severity", "title", "body", "suggestion")
REVIEW_EVENT = "COMMENT"  # never APPROVE or REQUEST_CHANGES
BOT_LOGIN = "github-actions[bot]"
MAX_BODY = 60000  # GitHub rejects comment bodies over 65536 characters

SEVERITY_LABEL = {"blocker": "Blocker", "major": "Major", "minor": "Minor", "nit": "Nit"}


class ValidationError(Exception):
    def __init__(self, errors):
        super().__init__("; ".join(errors))
        self.errors = list(errors)


# --------------------------------------------------------------------------- validation


def _strip_fences(text: str) -> str:
    """Accept a JSON object wrapped in a ```json fence, which models sometimes add despite instructions."""
    t = text.strip()
    m = re.fullmatch(r"```(?:json)?\s*\n(.*)\n```", t, re.DOTALL)
    return m.group(1) if m else t


def parse_review(text: str) -> dict:
    """Parse and validate review JSON. Raises ValidationError listing every problem found."""
    try:
        data = json.loads(_strip_fences(text))
    except ValueError as e:
        raise ValidationError(["not valid JSON: %s" % e])
    validate(data)
    return data


def validate(data) -> None:
    errors = []
    if not isinstance(data, dict):
        raise ValidationError(["top level must be an object"])
    for key in ("verdict", "summary", "findings"):
        if key not in data:
            errors.append("missing key %r" % key)
    for key in data:
        if key not in ("verdict", "summary", "findings"):
            errors.append("unexpected key %r" % key)
    if "verdict" in data and data["verdict"] not in VERDICTS:
        errors.append("verdict must be one of %s, got %r" % ("/".join(VERDICTS), data["verdict"]))
    if "summary" in data and not isinstance(data["summary"], str):
        errors.append("summary must be a string")
    findings = data.get("findings", [])
    if not isinstance(findings, list):
        errors.append("findings must be an array")
        findings = []
    if len(findings) > MAX_FINDINGS:
        errors.append("findings has %d items, at most %d allowed" % (len(findings), MAX_FINDINGS))
    for i, f in enumerate(findings):
        where = "findings[%d]" % i
        if not isinstance(f, dict):
            errors.append("%s must be an object" % where)
            continue
        for key in FINDING_KEYS:
            if key not in f:
                errors.append("%s missing key %r" % (where, key))
        for key in f:
            if key not in FINDING_KEYS:
                errors.append("%s unexpected key %r" % (where, key))
        for key in ("path", "title", "body", "suggestion"):
            if key in f and not isinstance(f[key], str):
                errors.append("%s.%s must be a string" % (where, key))
        if "line" in f and (not isinstance(f["line"], int) or isinstance(f["line"], bool) or f["line"] < 0):
            errors.append("%s.line must be a non-negative integer" % where)
        if "severity" in f and f["severity"] not in SEVERITIES:
            errors.append("%s.severity must be one of %s, got %r" % (where, "/".join(SEVERITIES), f["severity"]))
    if errors:
        raise ValidationError(errors)


# --------------------------------------------------------------------------- diff parsing

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
_C_ESCAPES = {"n": "\n", "t": "\t", '"': '"', "\\": "\\", "a": "\a", "b": "\b", "f": "\f", "r": "\r", "v": "\v"}


def _unquote_path(raw: str) -> str:
    """Decode a path from a ---/+++ header: strip git's trailing tab and C-style quoting."""
    raw = raw.rstrip("\n").rstrip("\r")
    if raw.endswith("\t"):
        raw = raw[:-1]
    if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
        body, out, i = raw[1:-1], bytearray(), 0
        while i < len(body):
            c = body[i]
            if c == "\\" and i + 1 < len(body):
                n = body[i + 1]
                if re.fullmatch(r"[0-7]{3}", body[i + 1:i + 4]):  # octal byte, e.g. \303\251 for UTF-8 "é"
                    out.append(int(body[i + 1:i + 4], 8))
                    i += 4
                    continue
                out.extend(_C_ESCAPES.get(n, n).encode("utf-8"))
                i += 2
                continue
            out.extend(c.encode("utf-8"))
            i += 1
        raw = out.decode("utf-8", "replace")
    return raw


def commentable_lines(diff_text: str) -> dict:
    """Map each file in a unified diff to the set of NEW-side line numbers a review comment can target.

    Those are the added ('+') and context (' ') lines inside hunks, i.e. what GitHub accepts with side=RIGHT.
    Deleted files and binary files contribute nothing.
    """
    result = {}
    path = None
    new_line = 0
    remaining = 0  # new-side lines left in the current hunk
    old_remaining = 0
    for line in diff_text.splitlines():
        if remaining <= 0 and old_remaining <= 0:
            if line.startswith("+++ "):
                target = _unquote_path(line[4:])
                if target == "/dev/null":
                    path = None
                else:
                    path = target[2:] if target.startswith("b/") else target
                    result.setdefault(path, set())
                continue
            m = _HUNK.match(line)
            if m and path is not None:
                new_line = int(m.group(1))
                remaining = int(m.group(2)) if m.group(2) is not None else 1
                old = re.match(r"^@@ -\d+(?:,(\d+))?", line)
                old_remaining = int(old.group(1)) if old and old.group(1) is not None else 1
            elif line.startswith("diff --git "):
                path = None
            continue
        # inside a hunk
        if line.startswith("\\"):  # "\ No newline at end of file"
            continue
        tag = line[:1]
        if tag == "+":
            result[path].add(new_line)
            new_line += 1
            remaining -= 1
        elif tag == "-":
            old_remaining -= 1
        else:  # context line (' ' or an empty line from an editor that stripped the space)
            result[path].add(new_line)
            new_line += 1
            remaining -= 1
            old_remaining -= 1
    return result


# --------------------------------------------------------------------------- rendering


def _neutralise(text: str) -> str:
    """Model output is untrusted: stop it pinging people or forging our marker."""
    text = text.replace("<!--", "&lt;!--")
    return re.sub(r"(?<![\w`])@(?=[A-Za-z0-9])", "@​", text)


def _truncate(text: str, limit: int = MAX_BODY) -> str:
    return text if len(text) <= limit else text[: limit - 40] + "\n\n_(truncated)_"


def split_findings(review: dict, lines: dict):
    """Return (inline, other): findings that can be posted on a diff line, and the rest."""
    order = {s: i for i, s in enumerate(SEVERITIES)}
    findings = sorted(review["findings"], key=lambda f: order[f["severity"]])
    inline, other = [], []
    for f in findings:
        path = f["path"].strip().lstrip("/")
        if path.startswith(("a/", "b/")) and path not in lines:
            path = path[2:]
        if f["line"] > 0 and f["line"] in lines.get(path, ()):
            inline.append(dict(f, path=path))
        else:
            other.append(f)
    return inline[:MAX_FINDINGS], other


def render_inline(f: dict) -> str:
    parts = ["**%s: %s**" % (SEVERITY_LABEL[f["severity"]], _neutralise(f["title"])), "", _neutralise(f["body"])]
    if f["suggestion"].strip():
        parts += ["", "**Suggestion:** " + _neutralise(f["suggestion"])]
    return _truncate("\n".join(parts))


def render_summary(review: dict, inline: list, other: list, *, provider: str = "", model: str = "",
                   sha: str = "", note: str = "") -> str:
    verdict = "Looks good" if review["verdict"] == "looks_good" else "Needs changes"
    counts = {s: 0 for s in SEVERITIES}
    for f in review["findings"]:
        counts[f["severity"]] += 1
    tally = ", ".join("%d %s" % (n, s) for s, n in counts.items() if n) or "no findings"
    who = provider + (" (%s)" % model if model else "") if provider else "AI"
    lines = [MARKER, "## AI review: %s" % verdict, "",
             "_%s review of %s. Advisory only: it never approves or blocks. %s._"
             % (who, ("`%s`" % sha[:12]) if sha else "this pull request", tally), "",
             _neutralise(review["summary"].strip())]
    if inline:
        lines += ["", "%d finding(s) are posted as inline comments on the diff." % len(inline)]
    if other:
        lines += ["", "### Findings not on a diff line", ""]
        for f in other:
            loc = f["path"] + (":%d" % f["line"] if f["line"] > 0 else "")
            lines.append("- **%s** `%s`: **%s**. %s" % (SEVERITY_LABEL[f["severity"]], loc.replace("`", "'"),
                                                        _neutralise(f["title"]), _neutralise(f["body"])))
            if f["suggestion"].strip():
                lines.append("  Suggestion: " + _neutralise(f["suggestion"]))
    if note:
        lines += ["", note]
    lines += ["", "<sub>Re-run with the `ai-rereview` label; skip with `ai-review-skip`.</sub>"]
    return _truncate("\n".join(lines))


def render_failure(errors: list, sha: str = "") -> str:
    shown = "\n".join("- " + _neutralise(e) for e in errors[:20])
    return "\n".join([MARKER, "## AI review: output was invalid", "",
                      "The reviewer's output for %s did not match `.github/ai-review/review.schema.json`, so nothing "
                      "was posted inline:" % (("`%s`" % sha[:12]) if sha else "this commit"), "", shown])


def build_review_payload(inline: list, sha: str) -> dict:
    return {
        "commit_id": sha,
        "event": REVIEW_EVENT,
        "body": MARKER.replace("review", "review-inline") + "\nAI review: %d inline finding(s). "
                "Summary in the sticky comment." % len(inline),
        "comments": [{"path": f["path"], "line": f["line"], "side": "RIGHT", "body": render_inline(f)}
                     for f in inline],
    }


# --------------------------------------------------------------------------- GitHub API


class GitHub:
    def __init__(self, token: str, repo: str, api: str = "https://api.github.com"):
        self.token, self.repo, self.api = token, repo, api.rstrip("/")

    def request(self, method: str, path: str, body=None):
        url = path if path.startswith("http") else "%s/repos/%s%s" % (self.api, self.repo, path)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": "Bearer " + self.token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "spendlint-ai-review",
            "Content-Type": "application/json",
        })
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            link = resp.headers.get("Link", "")
        return (json.loads(raw) if raw else None), link

    def find_sticky(self, pr: int):
        url = "/issues/%d/comments?per_page=100" % pr
        while url:
            comments, link = self.request("GET", url)
            for c in comments:
                if MARKER in (c.get("body") or "") and (c.get("user") or {}).get("login") == BOT_LOGIN:
                    return c["id"]
            m = re.search(r'<([^>]+)>;\s*rel="next"', link or "")
            url = m.group(1) if m else None
        return None

    def upsert_sticky(self, pr: int, body: str):
        cid = self.find_sticky(pr)
        if cid:
            self.request("PATCH", "/issues/comments/%d" % cid, {"body": body})
            return "updated", cid
        created, _ = self.request("POST", "/issues/%d/comments" % pr, {"body": body})
        return "created", created["id"]

    def create_review(self, pr: int, payload: dict):
        assert payload["event"] == REVIEW_EVENT
        created, _ = self.request("POST", "/pulls/%d/reviews" % pr, payload)
        return created["id"]


# --------------------------------------------------------------------------- main


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--review", required=True, help="review JSON written by the provider")
    ap.add_argument("--diff", required=True, help="unified diff of the pull request")
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    ap.add_argument("--pr", type=int, default=int(os.environ.get("PR_NUMBER") or 0))
    ap.add_argument("--sha", default=os.environ.get("HEAD_SHA", ""), help="head commit the review is for")
    ap.add_argument("--provider", default=os.environ.get("AI_PROVIDER", ""))
    ap.add_argument("--model", default=os.environ.get("AI_MODEL", ""))
    ap.add_argument("--validate-only", action="store_true", help="validate the JSON and exit")
    ap.add_argument("--dry-run", action="store_true", help="print what would be posted; no network")
    args = ap.parse_args(argv)

    try:
        with open(args.review, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as e:
        text = ""
        print("::error::cannot read %s: %s" % (args.review, e), file=sys.stderr)
    with open(args.diff, encoding="utf-8", errors="replace") as fh:
        lines = commentable_lines(fh.read())

    try:
        review = parse_review(text)
    except ValidationError as e:
        for err in e.errors:
            print("::error title=AI review JSON invalid::%s" % err, file=sys.stderr)
        if not (args.validate_only or args.dry_run) and args.repo and args.pr:
            gh = GitHub(os.environ["GITHUB_TOKEN"], args.repo, os.environ.get("GITHUB_API_URL", "https://api.github.com"))
            gh.upsert_sticky(args.pr, render_failure(e.errors, args.sha))
        return 1
    if args.validate_only:
        print("review JSON is valid: verdict=%s, %d finding(s)" % (review["verdict"], len(review["findings"])))
        return 0

    inline, other = split_findings(review, lines)
    meta = dict(provider=args.provider, model=args.model, sha=args.sha)
    payload = build_review_payload(inline, args.sha) if inline else None

    if args.dry_run:
        print(json.dumps({"summary_comment": render_summary(review, inline, other, **meta),
                          "review": payload}, indent=2))
        return 0

    if not (args.repo and args.pr and args.sha):
        print("::error::--repo, --pr and --sha are required to post", file=sys.stderr)
        return 2
    gh = GitHub(os.environ["GITHUB_TOKEN"], args.repo, os.environ.get("GITHUB_API_URL", "https://api.github.com"))
    note = ""
    if payload:
        try:
            rid = gh.create_review(args.pr, payload)
            print("posted review %s with %d inline comment(s)" % (rid, len(inline)))
        except urllib.error.HTTPError as e:
            # e.g. 422 when a line is no longer part of the diff because the branch moved on
            detail = e.read().decode("utf-8", "replace")[:500]
            print("::warning::inline review rejected (%s): %s" % (e.code, detail), file=sys.stderr)
            other, inline = inline + other, []
            note = "_Inline comments could not be posted (HTTP %d), so all findings are listed here._" % e.code
    action, cid = gh.upsert_sticky(args.pr, render_summary(review, inline, other, note=note, **meta))
    print("%s summary comment %s" % (action, cid))
    return 0


if __name__ == "__main__":
    sys.exit(main())
