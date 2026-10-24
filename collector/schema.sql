-- One row per spendlint run. Columns mirror the event schema in spendlint's docs/telemetry.md.
-- No IP addresses, user agents or other request metadata are stored.
CREATE TABLE IF NOT EXISTS events (
  id                 INTEGER PRIMARY KEY,
  received_day       TEXT    NOT NULL,  -- UTC date the server received the event (YYYY-MM-DD)
  event_hour         TEXT    NOT NULL,  -- client timestamp, hour resolution
  install_id         TEXT    NOT NULL,  -- random uuid created on the user's machine
  tool_version       TEXT    NOT NULL,
  python_version     TEXT    NOT NULL,
  os_family          TEXT    NOT NULL,
  ci_provider        TEXT    NOT NULL,
  command            TEXT    NOT NULL,
  runtime_seconds    REAL    NOT NULL,
  files_python       INTEGER NOT NULL,
  files_ts           INTEGER NOT NULL,
  findings_total     INTEGER NOT NULL,
  findings_by_rule   TEXT    NOT NULL,  -- JSON object: rule id -> count
  exit_code          INTEGER NOT NULL,
  license_key_sha256 TEXT               -- only when the user set SPENDLINT_LICENSE_KEY
);
