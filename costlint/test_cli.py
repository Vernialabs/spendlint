"""Run: python -m unittest costlint.test_cli   (CLI flags, config, suppression, formats, exit codes, git diff)"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from . import __version__
from .action import main as action_main
from .cli import main
from .config import ConfigError, load_config, parse_simple_yaml
from .gitdiff import added_lines
from .scan import scan_repo, suppressed_rules

# R-CTX-008 (info) on line 2, R-CACHE-003 (warning) on line 4
SRC = ("import json\n"
       "p = f'Data: {json.dumps(d, indent=2)}'\n"
       "from datetime import datetime\n"
       "SYSTEM_PROMPT = f'You are helpful. Now: {datetime.now()}'\n")


def run_cli(*args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(list(args))
    return code, out.getvalue(), err.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.data = Path(tempfile.mkdtemp())
        env = {k: v for k, v in os.environ.items() if not k.startswith(("COSTLINT_", "GITHUB_", "INPUT_", "RUNNER_"))}
        env.update(COSTLINT_DATA_DIR=str(self.data), COSTLINT_TELEMETRY="off")
        self.env = mock.patch.dict(os.environ, env, clear=True)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.td, ignore_errors=True)
        shutil.rmtree(self.data, ignore_errors=True)

    def write(self, rel, text):
        p = self.td / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p


class TestVersionAndExitCodes(Base):
    def test_version(self):
        code, out, _ = run_cli("--version")
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), f"costlint {__version__}")

    def test_fail_on_levels(self):
        self.write("a.py", SRC)
        self.assertEqual(run_cli(str(self.td))[0], 0)                                   # default none
        self.assertEqual(run_cli(str(self.td), "--fail-on", "none")[0], 0)
        self.assertEqual(run_cli(str(self.td), "--fail-on", "info")[0], 1)
        self.assertEqual(run_cli(str(self.td), "--fail-on", "warning")[0], 1)            # R-CACHE-003 is a warning
        self.assertEqual(run_cli(str(self.td), "--fail-on", "warning", "--rules", "R-CTX-008")[0], 0)  # info only
        self.assertEqual(run_cli(str(self.td), "--fail-on", "info", "--rules", "R-CTX-008")[0], 1)

    def test_clean_repo_exits_zero_even_with_fail_on(self):
        self.write("a.py", "x = 1\n")
        self.assertEqual(run_cli(str(self.td), "--fail-on", "info")[0], 0)

    def test_usage_errors_exit_two(self):
        self.assertEqual(run_cli(str(self.td / "missing"))[0], 2)
        self.assertEqual(run_cli(str(self.td), "--rules", "R-NOPE-001")[0], 2)
        self.assertEqual(run_cli(str(self.td), "--fail-on", "error")[0], 2)
        self.assertEqual(run_cli(str(self.td), "--min-confidence", "3")[0], 2)
        self.assertEqual(run_cli()[0], 2)

    def test_bad_config_exits_two(self):
        self.write("a.py", SRC)
        self.write(".costlint.json", '{"disable": ["R-NOT-REAL"]}')
        code, _, err = run_cli(str(self.td))
        self.assertEqual(code, 2)
        self.assertIn("unknown rule id", err)

    def test_scan_alias(self):
        self.write("a.py", SRC)
        self.assertEqual(run_cli("scan", str(self.td), "--fail-on", "info")[0], 1)


class TestConfig(Base):
    def test_yaml_disable_ignore_min_confidence(self):
        self.write("a.py", SRC)
        self.write("legacy/b.py", SRC)
        self.write(".costlint.yml", "# comment\ndisable: [R-CTX-008]\nignore_paths:\n  - legacy/*\nmin_confidence: 0.1\n")
        code, out, _ = run_cli(str(self.td), "--format", "jsonl")
        found = [json.loads(l) for l in out.splitlines()]
        self.assertEqual([(f["rule_id"], f["path"]) for f in found], [("R-CACHE-003", "a.py")])

    def test_json_config_and_explicit_path(self):
        self.write("a.py", SRC)
        cfg = self.write("cfg/costlint.json", json.dumps({"disable": ["R-CACHE-003"]}))
        _, out, _ = run_cli(str(self.td), "--config", str(cfg), "--format", "jsonl")
        self.assertEqual({json.loads(l)["rule_id"] for l in out.splitlines()}, {"R-CTX-008"})

    def test_min_confidence_from_config_and_flag_override(self):
        self.write("a.py", SRC)
        self.write(".costlint.json", '{"min_confidence": 0.99}')
        self.assertEqual(run_cli(str(self.td), "--format", "jsonl")[1].strip(), "")
        _, out, _ = run_cli(str(self.td), "--format", "jsonl", "--min-confidence", "0")
        self.assertEqual(len(out.splitlines()), 2)

    def test_no_config_flag(self):
        self.write("a.py", SRC)
        self.write(".costlint.json", '{"disable": ["R-CACHE-003", "R-CTX-008"]}')
        self.assertEqual(run_cli(str(self.td), "--fail-on", "info")[0], 0)
        self.assertEqual(run_cli(str(self.td), "--fail-on", "info", "--no-config")[0], 1)

    def test_directory_pattern(self):
        self.write("gen/x/b.py", SRC)
        self.write(".costlint.yml", "ignore_paths: [gen/]\n")
        self.assertEqual(run_cli(str(self.td), "--fail-on", "info")[0], 0)

    def test_simple_yaml_reader(self):
        self.assertEqual(parse_simple_yaml("disable:\n  - R-OUT-001\n  - 'R-MODEL-001'  # c\nmin_confidence: 0.5\n"),
                         {"disable": ["R-OUT-001", "R-MODEL-001"], "min_confidence": 0.5})
        with self.assertRaises(ConfigError):
            parse_simple_yaml("nested:\n  key: value\n")

    def test_validation(self):
        for text in ('{"surprise": 1}', '{"min_confidence": 2}', '{"ignore_paths": [1]}', '[]'):
            p = self.write(".costlint.json", text)
            with self.assertRaises(ConfigError):
                load_config(p)


class TestSuppression(Base):
    def test_same_line_and_line_above(self):
        self.write("a.py", "import json\n"
                           "p = f'Data: {json.dumps(d, indent=2)}'  # costlint: ignore R-CTX-008 debug only\n"
                           "# costlint: ignore R-CACHE-003 day-level is fine here\n"
                           "SYSTEM_PROMPT = f'Now: {datetime.now()}'\n")
        findings, stats = scan_repo(self.td)
        self.assertEqual(findings, [])
        self.assertEqual(stats["suppressed"], 2)

    def test_other_rule_id_does_not_suppress(self):
        self.write("a.py", "p = f'Data: {json.dumps(d, indent=2)}'  # costlint: ignore R-OUT-001\n")
        self.assertEqual({f["rule_id"] for f in scan_repo(self.td)[0]}, {"R-CTX-008"})

    def test_bare_ignore_needs_a_rule_id(self):
        self.write("a.py", "p = f'Data: {json.dumps(d, indent=2)}'  # costlint: ignore\n")
        self.assertEqual(len(scan_repo(self.td)[0]), 1)

    def test_typescript_comment(self):
        self.write("a.ts", "// costlint: ignore R-CACHE-003 rounded upstream\n"
                           "const systemPrompt = `You are X. Today is ${new Date().toISOString()}`;\n")
        self.assertEqual(scan_repo(self.td)[0], [])

    def test_parse_list_and_reason(self):
        self.assertEqual(suppressed_rules("x  # costlint: ignore R-OUT-001, R-CTX-008 reason R-LOOP-001"),
                         {"R-OUT-001", "R-CTX-008"})


class TestFormats(Base):
    def test_github_annotations(self):
        self.write("src/a.py", SRC)
        _, out, _ = run_cli(str(self.td), "--format", "github")
        lines = out.splitlines()
        self.assertEqual(len(lines), 2)
        warn = [l for l in lines if l.startswith("::warning ")][0]
        self.assertIn("file=src/a.py,line=4,endLine=4,title=R-CACHE-003 ", warn)
        note = [l for l in lines if l.startswith("::notice ")][0]
        self.assertIn("file=src/a.py,line=2", note)
        for l in lines:  # message part has no raw newlines and the property part no raw ':' or ','
            props = l.split("::")[1]
            self.assertNotIn("\n", l)
            self.assertEqual(props.split(" ", 1)[1].count(":"), 0)

    def test_summary_markdown_appends(self):
        self.write("a.py", SRC)
        summary = self.td / "summary.md"
        summary.write_text("previous step\n", encoding="utf-8")
        run_cli(str(self.td), "--summary-md", str(summary))
        text = summary.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("previous step\n## costlint"))
        self.assertIn("| R-CACHE-003 |", text)
        self.assertIn("| warning | R-CACHE-003 | `a.py:4` |", text)
        self.assertIn("**2 finding(s)**", text)

    def test_summary_markdown_clean(self):
        self.write("a.py", "x = 1\n")
        summary = self.td / "s.md"
        run_cli(str(self.td), "--summary-md", str(summary))
        self.assertIn("No cost findings.", summary.read_text(encoding="utf-8"))

    def test_sarif_side_output_and_version(self):
        self.write("a.py", SRC)
        sarif = self.td / "o.sarif"
        run_cli(str(self.td), "--sarif", str(sarif), "--out", str(self.td / "o.txt"))
        doc = json.loads(sarif.read_text(encoding="utf-8"))
        self.assertEqual(doc["runs"][0]["tool"]["driver"]["version"], __version__)
        self.assertEqual(len(doc["runs"][0]["results"]), 2)
        self.assertIn("2 finding(s)", (self.td / "o.txt").read_text(encoding="utf-8"))


@unittest.skipUnless(shutil.which("git"), "git not installed")
class TestGitDiff(Base):
    def git(self, *args):
        subprocess.run(["git", "-C", str(self.td), "-c", "user.email=t@example.invalid", "-c", "user.name=t", *args],
                       check=True, capture_output=True)

    def test_diff_base_reports_only_added_lines(self):
        self.write("pkg/old.py", SRC)                       # pre-existing findings: must not be reported
        self.git("init", "-q", "-b", "main")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "base")
        self.git("checkout", "-q", "-b", "feature")
        self.write("pkg/new.py", "x = 1\n" + SRC)            # two new findings, on lines 3 and 5
        self.write("pkg/old.py", SRC + "y = 2\n")            # a clean added line in a file with old findings
        self.git("add", ".")
        self.git("commit", "-q", "-m", "change")
        _, out, _ = run_cli(str(self.td), "--diff-base", "main", "--format", "jsonl")
        got = sorted((f["path"], f["line"]) for f in map(json.loads, out.splitlines()))
        self.assertEqual(got, [("pkg/new.py", 3), ("pkg/new.py", 5)])
        # scanning a subdirectory: paths stay relative to it
        _, out, _ = run_cli(str(self.td / "pkg"), "--diff-base", "main", "--format", "jsonl")
        self.assertEqual(sorted(json.loads(l)["path"] for l in out.splitlines()), ["new.py", "new.py"])

    def test_merge_commit_mode(self):
        self.write("old.py", SRC)
        self.git("init", "-q", "-b", "main")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "base")
        self.git("checkout", "-q", "-b", "feature")
        self.write("new.py", SRC)
        self.git("add", ".")
        self.git("commit", "-q", "-m", "change")
        self.git("checkout", "-q", "--detach", "main")
        self.git("merge", "-q", "--no-ff", "-m", "merge", "feature")   # like refs/pull/N/merge
        diff, note = added_lines(self.td, "main", merge_commit=True)
        self.assertEqual(set(diff), {"new.py"})
        self.assertIn("merge commit", note)

    def test_unknown_base_is_usage_error(self):
        self.write("a.py", "x = 1\n")
        self.git("init", "-q", "-b", "main")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "base")
        self.assertEqual(run_cli(str(self.td), "--diff-base", "origin/nope")[0], 2)


@unittest.skipUnless(shutil.which("git"), "git not installed")
class TestAction(Base):
    """`costlint action` as action.yml runs it, against a local git repository standing in for a PR checkout."""

    def git(self, *args):
        subprocess.run(["git", "-C", str(self.td), "-c", "user.email=t@example.invalid", "-c", "user.name=t", *args],
                       check=True, capture_output=True)

    def make_pr(self):
        self.write("app/old.py", SRC)
        self.git("init", "-q", "-b", "main")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "base")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")   # what actions/checkout leaves behind
        self.git("checkout", "-q", "-b", "feature")
        self.write("app/new.py", "x = 1\n" + SRC)
        self.git("add", ".")
        self.git("commit", "-q", "-m", "change")

    def run_action(self, **env):
        gh = self.td / ".gh"
        gh.mkdir(exist_ok=True)
        base = dict(GITHUB_ACTIONS="true", GITHUB_WORKSPACE=str(self.td), GITHUB_EVENT_NAME="pull_request",
                    GITHUB_BASE_REF="main", GITHUB_STEP_SUMMARY=str(gh / "summary.md"), GITHUB_OUTPUT=str(gh / "out"),
                    RUNNER_TEMP=str(gh))
        base.update(env)
        out = io.StringIO()
        with mock.patch.dict(os.environ, base), redirect_stdout(out), redirect_stderr(io.StringIO()):
            code = action_main([])
        outputs = dict(l.split("=", 1) for l in (gh / "out").read_text(encoding="utf-8").splitlines()) if (gh / "out").exists() else {}
        summary = (gh / "summary.md").read_text(encoding="utf-8") if (gh / "summary.md").exists() else ""
        return code, out.getvalue(), outputs, summary

    def test_pull_request_reports_only_added_lines(self):
        self.make_pr()
        code, out, outputs, summary = self.run_action()
        self.assertEqual(code, 0)
        annotations = [l for l in out.splitlines() if l.startswith("::")]
        self.assertEqual(len(annotations), 2)
        self.assertTrue(all("file=app/new.py" in a for a in annotations))
        self.assertEqual(outputs["findings-count"], "2")
        self.assertTrue(Path(outputs["sarif-path"]).is_file())
        self.assertIn("lines added since the merge base with origin/main", summary)

    def test_subdirectory_paths_are_repo_relative(self):
        self.make_pr()
        _, out, _, _ = self.run_action(INPUT_PATH="app")
        self.assertIn("file=app/new.py,line=3", out)
        doc = json.loads(Path(self.td / ".gh" / "costlint.sarif").read_text(encoding="utf-8"))
        self.assertEqual({r["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] for r in doc["runs"][0]["results"]},
                         {"app/new.py"})

    def test_push_event_scans_everything_and_fail_on(self):
        self.make_pr()
        code, _, outputs, _ = self.run_action(GITHUB_EVENT_NAME="push", INPUT_FAIL_ON="warning")
        self.assertEqual((code, outputs["findings-count"]), (1, "4"))
        self.assertEqual(self.run_action(INPUT_DIFF_MODE="off", INPUT_FAIL_ON="info", INPUT_RULES="R-CTX-008")[0], 1)
        self.assertEqual(self.run_action(INPUT_FAIL_ON="warning", INPUT_RULES="R-CTX-008")[0], 0)

    def test_bad_inputs_exit_two(self):
        self.make_pr()
        self.assertEqual(self.run_action(INPUT_FAIL_ON="sometimes")[0], 2)
        self.assertEqual(self.run_action(INPUT_DIFF_MODE="maybe")[0], 2)
        self.assertEqual(self.run_action(INPUT_MIN_CONFIDENCE="high")[0], 2)
        self.assertEqual(self.run_action(INPUT_DIFF_MODE="on", GITHUB_EVENT_NAME="push", GITHUB_BASE_REF="")[0], 2)

    def test_auto_mode_falls_back_to_full_scan_with_a_warning(self):
        self.make_pr()
        code, out, outputs, summary = self.run_action(GITHUB_BASE_REF="no-such-branch")
        self.assertEqual(code, 0)
        self.assertIn("::warning title=costlint::Could not compute the pull request diff", out)
        self.assertEqual(outputs["findings-count"], "4")
        self.assertIn("full scan (pull request diff unavailable)", summary)


if __name__ == "__main__":
    unittest.main()
