"""Coarse, opt-out usage telemetry. See docs/telemetry.md for the event schema and the reasoning.

What an event can contain is fixed by `build_event`: counts, a tool/python version, an OS family, a CI
provider name from a fixed list, rule ids from spendlint's own rule table, and a random install id.
It never receives file paths, file names, repository or branch names, commit messages, source code or
finding text, so it cannot leak them. `validate_event` re-checks every event against the schema before
it is written.

Switches, strongest first:
  DO_NOT_TRACK=1                      always off
  SPENDLINT_TELEMETRY=off|on           explicit choice (the Action's `telemetry` input sets this)
  CI detected                         off unless SPENDLINT_TELEMETRY=on
  `spendlint telemetry off|on`         stored local preference
  otherwise (local CLI)               on, after a one-time notice; the run that shows the notice records nothing

Events are appended to a local JSONL log (`spendlint telemetry show`) and sent to DEFAULT_URL, Vernia's
collector. SPENDLINT_TELEMETRY_URL points them elsewhere; SPENDLINT_TELEMETRY_URL=none keeps them local only.
Sending uses a short timeout and swallows every error: telemetry can never change a scan's result or exit code.
"""

import contextlib
import hashlib
import json
import os
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from . import __version__
from .meta import RULES

SCHEMA_VERSION = 1
SEND_TIMEOUT = 1.5
DEFAULT_URL = "https://t.vernia.in/v1/events"
LOCAL_ONLY = {"none", "off", "local"}
NOTICE_VERSION = 2  # 2: events are sent to DEFAULT_URL. Anyone who saw an older notice sees the new one once.
TRUE = {"1", "true", "yes", "on"}
FALSE = {"0", "false", "no", "off"}
COMMANDS = ("scan", "action")
CI_PROVIDERS = ("github-actions", "gitlab", "circle", "other-ci", "none")
OS_FAMILIES = ("windows", "linux", "macos", "other")
LANG_BUCKETS = ("python", "typescript_javascript")
EVENT_KEYS = {
    "schema_version",
    "event",
    "timestamp",
    "install_id",
    "tool_version",
    "python_version",
    "os_family",
    "ci_provider",
    "command",
    "runtime_seconds",
    "files_scanned",
    "findings_by_rule",
    "exit_code",
}
OPTIONAL_KEYS = {"license_key_sha256"}

NOTICE = """\
spendlint collects anonymous usage statistics to learn which rules are useful. Each run records:
  tool version, Python version (major.minor), OS family, CI provider name, command (scan/action),
  runtime in seconds, number of files scanned per language, number of findings per rule id, exit code,
  and a random install id created on this machine.
It never records source code, file or directory names, repository or branch names, commit messages
or finding text. Events are written to a local log you can read with `spendlint telemetry show`
and sent to https://t.vernia.in, which stores only these fields (not your IP address).
Turn it off with `spendlint telemetry off`, SPENDLINT_TELEMETRY=off, or DO_NOT_TRACK=1.
This notice is shown once; this run was not recorded.
"""


def data_dir():
    """Per-user data directory (override with SPENDLINT_DATA_DIR)."""
    if os.environ.get("SPENDLINT_DATA_DIR"):
        return Path(os.environ["SPENDLINT_DATA_DIR"])
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    elif sys.platform == "darwin":
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return Path(base) / "spendlint"


def log_path():
    return Path(os.environ.get("SPENDLINT_TELEMETRY_LOG") or data_dir() / "telemetry.jsonl")


def _state_path():
    return data_dir() / "telemetry.json"


def load_state():
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(state):
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=1), encoding="utf-8")


def ci_provider(env=None):
    env = os.environ if env is None else env
    if env.get("GITHUB_ACTIONS") == "true":
        return "github-actions"
    if env.get("GITLAB_CI"):
        return "gitlab"
    if env.get("CIRCLECI"):
        return "circle"
    if env.get("CI", "").lower() in TRUE or (env.get("BUILD_ID") and env.get("JENKINS_URL")) or env.get("TF_BUILD"):
        return "other-ci"
    return "none"


def os_family():
    return {"win32": "windows", "darwin": "macos"}.get(
        sys.platform, "linux" if sys.platform.startswith("linux") else "other"
    )


def decision(env=None, state=None):
    """(enabled, reason, needs_notice). Pure function of the environment and the stored state."""
    env = os.environ if env is None else env
    state = load_state() if state is None else state
    if env.get("DO_NOT_TRACK", "").strip().lower() in TRUE:
        return False, "DO_NOT_TRACK is set", False
    explicit = env.get("SPENDLINT_TELEMETRY", "").strip().lower()
    if explicit in FALSE:
        return False, "SPENDLINT_TELEMETRY=off", False
    if explicit in TRUE:
        return True, "SPENDLINT_TELEMETRY=on", False
    if ci_provider(env) != "none":
        return False, "running in CI (off unless SPENDLINT_TELEMETRY=on)", False
    if state.get("enabled") is False:
        return False, "turned off with `spendlint telemetry off`", False
    if state.get("enabled") is True:
        return True, "turned on with `spendlint telemetry on`", False
    if state.get("notice_version", 0) < NOTICE_VERSION:
        return False, "first run: notice not yet shown", True
    return True, "default for local use, notice already shown", False


def _install_id(state):
    iid = state.get("install_id")
    try:
        return str(uuid.UUID(iid))
    except (TypeError, ValueError):
        state["install_id"] = str(uuid.uuid4())  # random, never derived from hardware or identity
        return state["install_id"]


def build_event(command, runtime_seconds, stats, findings, exit_code, install_id, env=None):
    """The only place an event is assembled. Inputs are reduced to counts and fixed vocabularies here."""
    env = os.environ if env is None else env
    by_rule = {}
    for f in findings:
        rid = f.get("rule_id")
        if rid in RULES:
            by_rule[rid] = by_rule.get(rid, 0) + 1
    event = {
        "schema_version": SCHEMA_VERSION,
        "event": "run",
        "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:00:00Z"),  # hour resolution
        "install_id": install_id,
        "tool_version": __version__,
        "python_version": f"{sys.version_info[0]}.{sys.version_info[1]}",
        "os_family": os_family(),
        "ci_provider": ci_provider(env),
        "command": command if command in COMMANDS else "scan",
        "runtime_seconds": round(float(runtime_seconds), 1),
        "files_scanned": {"python": int(stats.get("py", 0)), "typescript_javascript": int(stats.get("ts", 0))},
        "findings_by_rule": dict(sorted(by_rule.items())),
        "exit_code": int(exit_code),
    }
    key = env.get("SPENDLINT_LICENSE_KEY", "").strip()
    if key:  # paid-customer identification only when the user sets a license key; the key itself is never sent
        event["license_key_sha256"] = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    validate_event(event)
    return event


def validate_event(event):
    """Raise ValueError unless the event matches docs/telemetry.md exactly."""
    keys = set(event)
    if not keys >= EVENT_KEYS or keys - EVENT_KEYS - OPTIONAL_KEYS:
        raise ValueError(f"event keys {sorted(keys)} do not match the schema")
    uuid.UUID(event["install_id"])
    checks = [
        event["schema_version"] == SCHEMA_VERSION,
        event["event"] == "run",
        event["tool_version"] == __version__,
        event["os_family"] in OS_FAMILIES,
        event["ci_provider"] in CI_PROVIDERS,
        event["command"] in COMMANDS,
        isinstance(event["runtime_seconds"], float),
        isinstance(event["exit_code"], int),
        set(event["files_scanned"]) == set(LANG_BUCKETS),
        all(isinstance(v, int) for v in event["files_scanned"].values()),
        all(k in RULES and isinstance(v, int) for k, v in event["findings_by_rule"].items()),
        len(event["python_version"]) <= 5 and event["python_version"].replace(".", "").isdigit(),
        len(event["timestamp"]) == 20,
        "license_key_sha256" not in event or len(event["license_key_sha256"]) == 16,
    ]
    if not all(checks):
        raise ValueError("event values do not match the schema")


def endpoint(env=None):
    """Where events are sent, or None to keep them in the local log only."""
    env = os.environ if env is None else env
    url = env.get("SPENDLINT_TELEMETRY_URL", "").strip()
    if url.lower() in LOCAL_ONLY:
        return None
    return url or DEFAULT_URL


def _send(event, url):
    import urllib.request

    if not url.lower().startswith(("https://", "http://")):
        return False
    req = urllib.request.Request(
        url,
        data=json.dumps(event).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "User-Agent": f"spendlint/{__version__}"},
    )
    with urllib.request.urlopen(req, timeout=SEND_TIMEOUT) as r:
        return 200 <= r.status < 300


def record(command, runtime_seconds, stats, findings, exit_code, stderr=None):
    """Record one run if telemetry is on. Never raises; returns the event written, or None."""
    try:
        state = load_state()
        enabled, _, needs_notice = decision(state=state)
        if needs_notice:
            (stderr or sys.stderr).write(NOTICE)
            state["notice_shown"] = True
            state["notice_version"] = NOTICE_VERSION
            save_state(state)
            return None
        if not enabled:
            return None
        had_id = "install_id" in state
        event = build_event(command, runtime_seconds, stats, findings, exit_code, _install_id(state))
        if not had_id:
            save_state(state)
        lp = log_path()
        lp.parent.mkdir(parents=True, exist_ok=True)
        with lp.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, sort_keys=True) + "\n")
        url = endpoint()
        if url:
            with contextlib.suppress(Exception):
                _send(event, url)
        return event
    except Exception:
        return None


def command(argv, out=None):
    """`spendlint telemetry status|show|on|off`."""
    out = out or sys.stdout
    sub = argv[0] if argv else "status"
    if sub in ("on", "off"):
        state = load_state()
        state["enabled"] = sub == "on"
        state["notice_shown"] = True
        state["notice_version"] = NOTICE_VERSION
        save_state(state)
        out.write(f"spendlint telemetry is now {sub} for this user (stored in {_state_path()}).\n")
        enabled, reason, _ = decision()
        if enabled != (sub == "on"):
            out.write(f"Note: the environment overrides this right now: {reason}.\n")
        return 0
    if sub == "status":
        state = load_state()
        enabled, reason, _ = decision(state=state)
        url = endpoint()
        out.write(f"telemetry: {'on' if enabled else 'off'} ({reason})\n")
        out.write(f"install id: {state.get('install_id', '(not created yet)')}\n")
        out.write(f"local log: {log_path()}\n")
        out.write(f"endpoint: {url if url else '(none: SPENDLINT_TELEMETRY_URL=none, nothing is sent)'}\n")
        out.write(
            f"license key: {'set (sent as a sha256 prefix)' if os.environ.get('SPENDLINT_LICENSE_KEY') else 'not set'}\n"
        )
        out.write(f"CI provider detected: {ci_provider()}\n")
        return 0
    if sub == "show":
        lp = log_path()
        if not lp.exists():
            out.write(f"no events recorded ({lp} does not exist)\n")
            return 0
        out.write(lp.read_text(encoding="utf-8"))
        return 0
    sys.stderr.write("usage: spendlint telemetry {status,show,on,off}\n")
    return 2


class Timer:
    def __init__(self):
        self.start = time.monotonic()

    def elapsed(self):
        return time.monotonic() - self.start
