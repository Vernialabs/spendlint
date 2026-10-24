"""Run: python -m unittest spendlint.test_telemetry

Proves the telemetry promises in docs/telemetry.md: no paths, names or code in an event; every opt-out works;
CI is off by default; a broken or slow endpoint never changes the exit code; nothing is sent without a URL.
"""
import io
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
import uuid
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from . import telemetry
from .action import main as action_main
from .cli import main

SECRETS = ["acme-secret-repo", "very_secret_module", "feature/secret-branch", "TOPSECRETTOKEN", "Leaky commit message",
           "hunter2-license", "secret_dir"]
SRC = ("import json\n"
       "TOPSECRETTOKEN = 'sk-do-not-leak'\n"
       "p = f'Data: {json.dumps(d, indent=2)}'\n"
       "from datetime import datetime\n"
       "SYSTEM_PROMPT = f'You are helpful. Now: {datetime.now()}'\n")
CI_VARS = ("CI", "GITHUB_ACTIONS", "GITLAB_CI", "CIRCLECI", "TF_BUILD", "JENKINS_URL", "BUILD_ID", "DO_NOT_TRACK")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.repo = self.tmp / "acme-secret-repo" / "secret_dir"
        self.repo.mkdir(parents=True)
        (self.repo / "very_secret_module.py").write_text(SRC, encoding="utf-8")
        self.data = self.tmp / "data"
        self.log = self.tmp / "data" / "events.jsonl"
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("SPENDLINT_", "GITHUB_", "INPUT_", "RUNNER_")) and k not in CI_VARS}
        env.update(SPENDLINT_DATA_DIR=str(self.data), SPENDLINT_TELEMETRY_LOG=str(self.log))
        self.patch = mock.patch.dict(os.environ, env, clear=True)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cli(self, *args, **extra_env):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, extra_env), redirect_stdout(out), redirect_stderr(err):
            code = main(list(args))
        return code, out.getvalue(), err.getvalue()

    def run_action(self, **extra_env):
        gh = self.tmp / "gh"
        gh.mkdir(exist_ok=True)
        env = dict(GITHUB_ACTIONS="true", CI="true", GITHUB_WORKSPACE=str(self.repo.parent), INPUT_PATH="secret_dir",
                   GITHUB_REPOSITORY="acme/acme-secret-repo", GITHUB_HEAD_REF="feature/secret-branch",
                   GITHUB_REF="refs/heads/feature/secret-branch", GITHUB_EVENT_NAME="push",
                   GITHUB_STEP_SUMMARY=str(gh / "summary.md"), GITHUB_OUTPUT=str(gh / "output"), RUNNER_TEMP=str(gh),
                   COMMIT_MESSAGE="Leaky commit message")
        env.update(extra_env)
        out = io.StringIO()
        with mock.patch.dict(os.environ, env), redirect_stdout(out), redirect_stderr(io.StringIO()):
            code = action_main([])
        return code, out.getvalue()

    def events(self):
        if not self.log.exists():
            return []
        return [json.loads(l) for l in self.log.read_text(encoding="utf-8").splitlines() if l.strip()]

    def assert_clean(self, text):
        for s in SECRETS + [str(self.tmp), self.tmp.name, "sk-do-not-leak", "json.dumps", ".py"]:
            self.assertNotIn(s, text, f"telemetry leaked {s!r}")


class TestEventContent(Base):
    def test_cli_event_has_no_paths_names_or_code(self):
        code, _, _ = self.run_cli(str(self.repo), "--fail-on", "info", SPENDLINT_TELEMETRY="on",
                                  SPENDLINT_LICENSE_KEY="hunter2-license")
        self.assertEqual(code, 1)
        raw = self.log.read_text(encoding="utf-8")
        self.assert_clean(raw)
        (ev,) = self.events()
        self.assertEqual(set(ev), telemetry.EVENT_KEYS | {"license_key_sha256"})
        telemetry.validate_event(ev)
        self.assertEqual(ev["command"], "scan")
        self.assertEqual(ev["ci_provider"], "none")
        self.assertEqual(ev["files_scanned"], {"python": 1, "typescript_javascript": 0})
        self.assertEqual(ev["findings_by_rule"], {"R-CACHE-003": 1, "R-CTX-008": 1})
        self.assertEqual(ev["exit_code"], 1)
        self.assertEqual(uuid.UUID(ev["install_id"]).version, 4)

    def test_action_event_has_no_repo_branch_or_paths(self):
        code, out = self.run_action(INPUT_TELEMETRY="on", INPUT_FAIL_ON="warning")
        self.assertEqual(code, 1)
        self.assertIn("secret_dir/very_secret_module.py", out)  # the annotation itself does name the file...
        raw = self.log.read_text(encoding="utf-8")
        self.assert_clean(raw)                                  # ...the telemetry event does not
        (ev,) = self.events()
        self.assertEqual((ev["command"], ev["ci_provider"], ev["exit_code"]), ("action", "github-actions", 1))

    def test_unknown_rule_ids_and_extra_fields_are_rejected(self):
        ev = telemetry.build_event("scan", 1.0, {"py": 1, "ts": 0}, [{"rule_id": "/home/me/secret.py"}], 0, str(uuid.uuid4()))
        self.assertEqual(ev["findings_by_rule"], {})
        ev["repo"] = "acme/x"
        with self.assertRaises(ValueError):
            telemetry.validate_event(ev)

    def test_install_id_is_random_and_stable(self):
        self.run_cli(str(self.repo), SPENDLINT_TELEMETRY="on")
        self.run_cli(str(self.repo), SPENDLINT_TELEMETRY="on")
        ids = {e["install_id"] for e in self.events()}
        self.assertEqual(len(ids), 1)
        other = Path(tempfile.mkdtemp())
        try:
            self.run_cli(str(self.repo), SPENDLINT_TELEMETRY="on", SPENDLINT_DATA_DIR=str(other))
        finally:
            shutil.rmtree(other, ignore_errors=True)
        self.assertEqual(len({e["install_id"] for e in self.events()}), 2)  # new data dir, new random id


class TestOptOuts(Base):
    def test_do_not_track_beats_explicit_on(self):
        self.run_cli(str(self.repo), SPENDLINT_TELEMETRY="on", DO_NOT_TRACK="1")
        self.run_action(INPUT_TELEMETRY="on", DO_NOT_TRACK="1")
        self.assertEqual(self.events(), [])
        self.assertNotIn("install_id", telemetry.load_state())

    def test_env_off(self):
        self.run_cli(str(self.repo), SPENDLINT_TELEMETRY="off")
        self.assertEqual(self.events(), [])

    def test_ci_is_off_by_default(self):
        for var in ({"GITHUB_ACTIONS": "true"}, {"GITLAB_CI": "true"}, {"CIRCLECI": "true"}, {"CI": "1"}):
            self.run_cli(str(self.repo), **var)
        code, _ = self.run_action()                 # telemetry input defaults to auto
        self.assertEqual(code, 0)
        self.assertEqual(self.events(), [])

    def test_ci_opt_in_via_env_and_action_input_off_wins(self):
        self.run_action(SPENDLINT_TELEMETRY="on")
        self.assertEqual(len(self.events()), 1)
        self.run_action(SPENDLINT_TELEMETRY="on", INPUT_TELEMETRY="off")
        self.assertEqual(len(self.events()), 1)

    def test_stored_preference(self):
        self.assertEqual(self.run_cli("telemetry", "off")[0], 0)
        self.run_cli(str(self.repo))
        self.run_cli(str(self.repo))
        self.assertEqual(self.events(), [])
        self.run_cli("telemetry", "on")
        self.run_cli(str(self.repo))
        self.assertEqual(len(self.events()), 1)

    def test_first_run_notice_once_and_that_run_not_recorded(self):
        _, out, err = self.run_cli(str(self.repo), "--format", "sarif")
        self.assertIn("spendlint collects anonymous usage statistics", err)
        json.loads(out)  # the notice never pollutes stdout (SARIF stays valid)
        self.assertEqual(self.events(), [])
        _, _, err2 = self.run_cli(str(self.repo))
        self.assertNotIn("anonymous usage", err2)
        self.assertEqual(len(self.events()), 1)

    def test_no_notice_in_ci(self):
        _, _, err = self.run_cli(str(self.repo), GITHUB_ACTIONS="true")
        self.assertEqual(err, "")


class Server:
    """Local HTTP endpoint on 127.0.0.1 with a chosen behaviour: ok, error or hang."""
    def __init__(self, behaviour):
        received = self.received = []

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                received.append(body)
                if behaviour == "hang":
                    time.sleep(5)
                self.send_response(500 if behaviour == "error" else 204)
                self.end_headers()

            def log_message(self, *a):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.httpd.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.httpd.server_port}/v1/events"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class TestSending(Base):
    def baseline(self):
        return self.run_cli(str(self.repo), "--fail-on", "warning", SPENDLINT_TELEMETRY="off")[0]

    def test_nothing_sent_without_url(self):
        with mock.patch("urllib.request.urlopen") as urlopen:
            self.run_cli(str(self.repo), SPENDLINT_TELEMETRY="on")
        urlopen.assert_not_called()
        self.assertEqual(len(self.events()), 1)

    def test_sent_body_equals_logged_event(self):
        srv = Server("ok")
        try:
            self.run_cli(str(self.repo), SPENDLINT_TELEMETRY="on", SPENDLINT_TELEMETRY_URL=srv.url)
        finally:
            srv.close()
        (body,) = srv.received
        self.assertEqual(json.loads(body), self.events()[0])
        self.assert_clean(body.decode("utf-8"))

    def test_failing_endpoints_do_not_change_exit_code(self):
        expected = self.baseline()
        self.assertEqual(expected, 1)
        srv = Server("error")
        closed = Server("ok")
        closed.close()  # nothing listens on this port any more: connection refused
        try:
            for url in (srv.url, closed.url, "http://[::1", "ftp://example.invalid/x", "not a url"):
                code, _, err = self.run_cli(str(self.repo), "--fail-on", "warning",
                                            SPENDLINT_TELEMETRY="on", SPENDLINT_TELEMETRY_URL=url)
                self.assertEqual(code, expected, url)
                self.assertNotIn("Traceback", err)
        finally:
            srv.close()

    def test_hanging_endpoint_is_cut_off_by_timeout(self):
        expected = self.baseline()
        srv = Server("hang")
        try:
            t = time.monotonic()
            code, _, _ = self.run_cli(str(self.repo), "--fail-on", "warning",
                                      SPENDLINT_TELEMETRY="on", SPENDLINT_TELEMETRY_URL=srv.url)
            elapsed = time.monotonic() - t
        finally:
            srv.close()
        self.assertEqual(code, expected)
        self.assertLess(elapsed, telemetry.SEND_TIMEOUT + 2.0)

    def test_action_exit_code_unchanged_by_failing_endpoint(self):
        expected, _ = self.run_action(INPUT_FAIL_ON="warning")
        code, _ = self.run_action(INPUT_FAIL_ON="warning", INPUT_TELEMETRY="on",
                                  SPENDLINT_TELEMETRY_URL="http://127.0.0.1:9/unreachable")
        self.assertEqual((expected, code), (1, 1))

    def test_internal_failure_is_swallowed(self):
        with mock.patch.object(telemetry, "build_event", side_effect=RuntimeError("boom")):
            code, _, err = self.run_cli(str(self.repo), "--fail-on", "warning", SPENDLINT_TELEMETRY="on")
        self.assertEqual(code, 1)
        self.assertNotIn("boom", err)


class TestSubcommand(Base):
    def test_status_and_show(self):
        code, out, _ = self.run_cli("telemetry", "status", SPENDLINT_TELEMETRY="on")
        self.assertEqual(code, 0)
        self.assertIn("telemetry: on (SPENDLINT_TELEMETRY=on)", out)
        self.assertIn("nothing is sent", out)
        self.assertIn("no events recorded", self.run_cli("telemetry", "show")[1])
        self.run_cli(str(self.repo), SPENDLINT_TELEMETRY="on")
        out = self.run_cli("telemetry", "show")[1]
        self.assertEqual(json.loads(out)["command"], "scan")
        self.assertIn("telemetry: off (DO_NOT_TRACK is set)", self.run_cli("telemetry", "status", DO_NOT_TRACK="1")[1])

    def test_bad_subcommand(self):
        self.assertEqual(self.run_cli("telemetry", "upload")[0], 2)


if __name__ == "__main__":
    unittest.main()
