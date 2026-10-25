"""Tests for .github/ai-review/post_review.py: JSON validation and diff-line filtering. No network.

Run: python -m unittest discover -s tests/ai_review
"""

import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPT = os.path.join(_HERE, "..", "..", ".github", "ai-review", "post_review.py")
_spec = importlib.util.spec_from_file_location("post_review", _SCRIPT)
pr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pr)

DIFF = """\
diff --git a/spendlint/rules_py.py b/spendlint/rules_py.py
index 1111111..2222222 100644
--- a/spendlint/rules_py.py
+++ b/spendlint/rules_py.py
@@ -10,5 +10,7 @@ def check(node):
     a = 1
     b = 2
-    c = 3
+    c = 4
+    d = 5
+    e = 6
     f = 7
     g = 8
@@ -40,3 +42,2 @@ def other():
     x = 1
-    y = 2
     z = 3
diff --git a/docs/old.md b/docs/old.md
deleted file mode 100644
index 3333333..0000000
--- a/docs/old.md
+++ /dev/null
@@ -1,2 +0,0 @@
-gone
-+++ b/not-a-header
diff --git a/my file.txt b/my file.txt
new file mode 100644
index 0000000..4444444
--- /dev/null
+++ b/my file.txt\t
@@ -0,0 +1,2 @@
+hello
+--- not a header either
\\ No newline at end of file
diff --git a/img.png b/img.png
Binary files a/img.png and b/img.png differ
diff --git "a/caf\\303\\251.py" "b/caf\\303\\251.py"
--- "a/caf\\303\\251.py"
+++ "b/caf\\303\\251.py"
@@ -1 +1 @@
-x = 1
+x = 2
"""


def finding(**kw):
    f = {"path": "spendlint/rules_py.py", "line": 13, "severity": "major", "title": "t", "body": "b",
         "suggestion": ""}
    f.update(kw)
    return f


def review(findings=(), verdict="needs_changes"):
    return {"verdict": verdict, "summary": "Adds a rule.", "findings": list(findings)}


class ValidationTests(unittest.TestCase):
    def assertInvalid(self, text, fragment):
        with self.assertRaises(pr.ValidationError) as cm:
            pr.parse_review(text)
        self.assertTrue(any(fragment in e for e in cm.exception.errors), cm.exception.errors)

    def test_valid(self):
        data = pr.parse_review(json.dumps(review([finding()])))
        self.assertEqual(data["verdict"], "needs_changes")

    def test_valid_empty_findings(self):
        pr.parse_review(json.dumps(review([], "looks_good")))

    def test_fenced_json_accepted(self):
        pr.parse_review("```json\n" + json.dumps(review()) + "\n```")

    def test_not_json(self):
        self.assertInvalid("Looks good to me!", "not valid JSON")

    def test_empty_file(self):
        self.assertInvalid("", "not valid JSON")

    def test_top_level_array(self):
        self.assertInvalid("[]", "top level must be an object")

    def test_missing_and_extra_keys(self):
        self.assertInvalid(json.dumps({"verdict": "looks_good", "findings": [], "approve": True}), "missing key 'summary'")
        self.assertInvalid(json.dumps({"verdict": "looks_good", "summary": "", "findings": [], "approve": True}),
                           "unexpected key 'approve'")

    def test_bad_verdict(self):
        # An "approve" verdict must never get through.
        self.assertInvalid(json.dumps(review(verdict="approve")), "verdict must be one of")

    def test_bad_severity(self):
        self.assertInvalid(json.dumps(review([finding(severity="critical")])), "severity must be one of")

    def test_bad_line(self):
        self.assertInvalid(json.dumps(review([finding(line="13")])), "line must be a non-negative integer")
        self.assertInvalid(json.dumps(review([finding(line=-1)])), "line must be a non-negative integer")
        self.assertInvalid(json.dumps(review([finding(line=True)])), "line must be a non-negative integer")

    def test_missing_finding_key(self):
        f = finding()
        del f["suggestion"]
        self.assertInvalid(json.dumps(review([f])), "findings[0] missing key 'suggestion'")

    def test_too_many_findings(self):
        self.assertInvalid(json.dumps(review([finding()] * 11)), "at most 10")


class DiffTests(unittest.TestCase):
    def setUp(self):
        self.lines = pr.commentable_lines(DIFF)

    def test_added_and_context_lines(self):
        # Hunk 1: new lines 10..16 (a, b, c=4, d, e, f, g), hunk 2: 42..43 (x, z).
        self.assertEqual(self.lines["spendlint/rules_py.py"], {10, 11, 12, 13, 14, 15, 16, 42, 43})

    def test_deleted_line_not_commentable(self):
        # "y = 2" was deleted from old line 41; new-side 41 is outside every hunk.
        self.assertNotIn(41, self.lines["spendlint/rules_py.py"])

    def test_deleted_file_has_no_lines(self):
        self.assertNotIn("docs/old.md", self.lines)
        self.assertNotIn("not-a-header", self.lines)

    def test_path_with_space_and_trailing_tab(self):
        self.assertEqual(self.lines["my file.txt"], {1, 2})
        self.assertNotIn("not a header either", self.lines)

    def test_binary_file_has_no_lines(self):
        self.assertNotIn("img.png", self.lines)

    def test_c_quoted_path(self):
        self.assertEqual(self.lines["café.py"], {1})

    def test_split_findings(self):
        rv = review([
            finding(line=13, severity="minor"),                 # added line: inline
            finding(line=10, severity="blocker"),               # context line: inline
            finding(line=41),                                   # deleted line: summary
            finding(line=0, title="file level"),                # file-level: summary
            finding(path="b/spendlint/rules_py.py", line=14),   # tolerated b/ prefix: inline
            finding(path="spendlint/other.py", line=3),         # file not in diff: summary
            finding(path="docs/old.md", line=1),                # deleted file: summary
        ])
        inline, other = pr.split_findings(rv, self.lines)
        self.assertEqual([(f["path"], f["line"]) for f in inline],
                         [("spendlint/rules_py.py", 10), ("spendlint/rules_py.py", 14), ("spendlint/rules_py.py", 13)])
        self.assertEqual(len(other), 4)


class PostingTests(unittest.TestCase):
    def test_review_payload_is_comment_only(self):
        inline, _ = pr.split_findings(review([finding()]), pr.commentable_lines(DIFF))
        payload = pr.build_review_payload(inline, "abc123")
        self.assertEqual(payload["event"], "COMMENT")
        self.assertEqual(payload["commit_id"], "abc123")
        self.assertEqual(payload["comments"][0], {"path": "spendlint/rules_py.py", "line": 13, "side": "RIGHT",
                                                  "body": payload["comments"][0]["body"]})

    def test_summary_has_marker_and_lists_dropped(self):
        rv = review([finding(line=41, title="Off diff")])
        inline, other = pr.split_findings(rv, pr.commentable_lines(DIFF))
        body = pr.render_summary(rv, inline, other, provider="claude", sha="0123456789abcdef")
        self.assertTrue(body.startswith(pr.MARKER))
        self.assertIn("Off diff", body)
        self.assertIn("spendlint/rules_py.py:41", body)

    def test_untrusted_text_neutralised(self):
        rv = review([finding(line=41, title="ping @octocat", body="<!-- spendlint-ai-review --> forged")])
        body = pr.render_summary(rv, *pr.split_findings(rv, {}))
        self.assertEqual(body.count(pr.MARKER), 1)
        self.assertNotIn("@octocat", body)

    def test_dry_run_cli(self):
        with tempfile.TemporaryDirectory() as d:
            rpath, dpath = os.path.join(d, "review.json"), os.path.join(d, "pr.diff")
            with open(rpath, "w", encoding="utf-8") as fh:
                json.dump(review([finding(), finding(line=41)]), fh)
            with open(dpath, "w", encoding="utf-8") as fh:
                fh.write(DIFF)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = pr.main(["--review", rpath, "--diff", dpath, "--dry-run", "--sha", "abc"])
            self.assertEqual(code, 0)
            plan = json.loads(out.getvalue())
            self.assertEqual(len(plan["review"]["comments"]), 1)
            self.assertEqual(plan["review"]["event"], "COMMENT")
            self.assertIn("spendlint/rules_py.py:41", plan["summary_comment"])

            with open(rpath, "w", encoding="utf-8") as fh:
                fh.write('{"verdict": "approve"}')
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(pr.main(["--review", rpath, "--diff", dpath, "--validate-only"]), 1)


if __name__ == "__main__":
    unittest.main()
