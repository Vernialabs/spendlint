# spendlint telemetry

spendlint records coarse usage statistics so we can learn which rules people keep, which they turn off, and
how long scans take. This page is the full contract: what an event contains, when one is recorded, and where it goes.
The code is `spendlint/telemetry.py`; the promises below are checked by `spendlint/test_telemetry.py`.

## Short version

- **Counts only, sent to Vernia.** Each event is appended to a local file and sent to `https://t.vernia.in`, a
  small collector whose full source is in [`collector/`](../collector). It stores the fields below and nothing else:
  not your IP address, not request headers. Set `SPENDLINT_TELEMETRY_URL=none` to keep events local only.
- **No code, paths or names, ever.** An event has no file paths, file names, directory names, repository names,
  branch names, commit messages, source code or finding text. The event builder never receives them.
- **Off in CI by default.** In GitHub Actions, GitLab CI, CircleCI or any environment with `CI=true`, nothing is
  recorded unless you opt in with `telemetry: on` (Action input) or `SPENDLINT_TELEMETRY=on`.
- **On for local CLI use, after a one-time notice.** The first local run prints what is collected and how to turn
  it off, and that run itself is not recorded.
- **Easy to turn off:** `spendlint telemetry off`, `SPENDLINT_TELEMETRY=off`, or `DO_NOT_TRACK=1`.

## When an event is recorded

Checked in this order; the first match decides:

| Condition | Result |
|---|---|
| `DO_NOT_TRACK` is `1`/`true`/`yes`/`on` | off (beats everything, including the Action input) |
| `SPENDLINT_TELEMETRY=off` (or `0`, `false`, `no`) | off |
| `SPENDLINT_TELEMETRY=on` (or `1`, `true`, `yes`) | on. The Action's `telemetry: on/off` input sets this variable for its step |
| A CI provider is detected | off |
| `spendlint telemetry off` / `on` was run | that stored choice |
| First local run (or first run after the notice changed) | prints the notice, records and sends nothing, remembers the notice was shown |
| Later local runs | on |

CI detection: `GITHUB_ACTIONS=true` (github-actions), `GITLAB_CI` (gitlab), `CIRCLECI` (circle),
`CI=true`, `TF_BUILD` or Jenkins' `JENKINS_URL`+`BUILD_ID` (other-ci).

The notice goes to stderr, so it never corrupts `--format sarif`/`jsonl` output on stdout.

## The event (schema version 1)

One JSON object per scan, one line per event in the log:

```json
{
  "schema_version": 1,
  "event": "run",
  "timestamp": "2026-10-24T13:00:00Z",
  "install_id": "3f1c2a9e-8d4b-4c1e-9a77-0b6e2f4d5c10",
  "tool_version": "0.1.0",
  "python_version": "3.11",
  "os_family": "windows",
  "ci_provider": "none",
  "command": "scan",
  "runtime_seconds": 0.4,
  "files_scanned": {"python": 12, "typescript_javascript": 0},
  "findings_by_rule": {"R-CACHE-003": 1, "R-CTX-008": 1},
  "exit_code": 0
}
```

| Field | Type | Values and meaning |
|---|---|---|
| `schema_version` | integer | always `1` for this layout |
| `event` | string | always `"run"` |
| `timestamp` | string | UTC, truncated to the hour (`YYYY-MM-DDTHH:00:00Z`) |
| `install_id` | string | random UUID4 created on first recorded run and stored in the data directory. Not derived from hardware, user name, e-mail or anything else. Deleting the data directory creates a new one. On ephemeral CI runners it is new every run. |
| `tool_version` | string | spendlint version (`spendlint.__version__`) |
| `python_version` | string | major.minor only, e.g. `"3.11"` |
| `os_family` | string | `windows`, `linux`, `macos`, `other` |
| `ci_provider` | string | `github-actions`, `gitlab`, `circle`, `other-ci`, `none` |
| `command` | string | `scan` (CLI) or `action` (GitHub Action) |
| `runtime_seconds` | number | wall time of the run, one decimal |
| `files_scanned` | object | `{"python": int, "typescript_javascript": int}`: file counts only |
| `findings_by_rule` | object | rule id to count. Keys can only be spendlint's own rule ids (`spendlint/meta.py`); anything else is dropped |
| `exit_code` | integer | 0 clean, 1 findings at or above `--fail-on`, 2 error |
| `license_key_sha256` | string, optional | present only when you set `SPENDLINT_LICENSE_KEY`: the first 16 hex characters of the key's SHA-256. The key itself is never written or sent. Never inferred from anything else. |

`validate_event` rejects any event with a missing or extra key, or a value outside these sets, before it is written.

### What is never collected

Source code, code snippets, finding details or messages, file paths, file names, directory names, repository names
or URLs, organisation or user names, branch names, commit SHAs or messages, environment variables, IP addresses or
anything derived from them, hardware ids. Like any web request, the connection to `t.vernia.in` carries your IP
address; the collector never reads or stores it, and its request logs are turned off.

## Where events go

1. **Local log, always first.** `SPENDLINT_TELEMETRY_LOG`, or by default `telemetry.jsonl` in the data directory:
   - Windows: `%LOCALAPPDATA%\spendlint\`
   - macOS: `~/Library/Application Support/spendlint/`
   - Linux: `$XDG_DATA_HOME/spendlint/` or `~/.local/share/spendlint/`
   - override the directory with `SPENDLINT_DATA_DIR`
   The same directory holds `telemetry.json` (install id, notice shown, stored on/off choice).
2. **Vernia's collector**, `https://t.vernia.in/v1/events`. `SPENDLINT_TELEMETRY_URL` sends events to a server of
   your choice instead (http or https); `SPENDLINT_TELEMETRY_URL=none` sends nothing and keeps only the local log.
   The event is POSTed as JSON with a 1.5 second timeout. Every error (DNS, refused connection, HTTP 500, timeout, malformed URL) is swallowed. Telemetry
   runs after the scan output is written and can never change the exit code. The sent body is byte-for-byte the event
   in the log.

## Commands

```bash
spendlint telemetry status   # on/off and why, install id, log path, endpoint
spendlint telemetry show     # print the local log
spendlint telemetry off      # store "off" for this user
spendlint telemetry on       # store "on" for this user
```

## GitHub Action

```yaml
- uses: vernialabs/spendlint@v0
  with:
    telemetry: on      # default auto = off in CI
```

With `telemetry: on`, each run's event is sent to the collector like a local run. With the default (`auto`), CI
runs record and send nothing.

## The collector

`collector/` in this repository is the exact code running at `t.vernia.in`: a Cloudflare Worker that checks each
event against this schema, rejects anything else (unknown keys, free text, oversized bodies), and writes one row to
a Cloudflare D1 database. `collector/schema.sql` is the full table.

## Paid customers

A paying company may want usage tied to its license. That is a separate, explicit setting: `SPENDLINT_LICENSE_KEY`.
When set, the event carries `license_key_sha256` (a hash prefix the server can match against keys it issued). It is
never inferred from a repository, organisation, e-mail or anything else.
