// Receives spendlint usage events and stores them in D1.
// The event schema is defined by spendlint/telemetry.py (build_event / validate_event) and docs/telemetry.md.
// Anything that does not match it exactly is rejected, so free-form data can never be stored.
// The client's IP address and headers are never read or stored.

const MAX_BODY = 4096;
const EVENT_KEYS = ["schema_version", "event", "timestamp", "install_id", "tool_version", "python_version",
  "os_family", "ci_provider", "command", "runtime_seconds", "files_scanned", "findings_by_rule", "exit_code"];
const OPTIONAL_KEYS = ["license_key_sha256"];
const OS_FAMILIES = ["windows", "linux", "macos", "other"];
const CI_PROVIDERS = ["github-actions", "gitlab", "circle", "other-ci", "none"];
const COMMANDS = ["scan", "action"];
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const HOUR = /^\d{4}-\d{2}-\d{2}T\d{2}:00:00Z$/;
const VERSION = /^\d{1,3}\.\d{1,3}\.\d{1,3}([a-z0-9.+-]{0,16})?$/;
const PY_VERSION = /^\d{1,2}\.\d{1,3}$/;
const RULE_ID = /^R-[A-Z]{2,8}-\d{3}$/;
const HEX16 = /^[0-9a-f]{16}$/;

const isCount = (v) => Number.isInteger(v) && v >= 0 && v <= 10_000_000;

export function validate(e) {
  if (!e || typeof e !== "object" || Array.isArray(e)) return "not an object";
  const keys = Object.keys(e);
  if (!EVENT_KEYS.every((k) => keys.includes(k))) return "missing keys";
  if (keys.some((k) => !EVENT_KEYS.includes(k) && !OPTIONAL_KEYS.includes(k))) return "unknown keys";
  if (e.schema_version !== 1 || e.event !== "run") return "unsupported schema";
  if (typeof e.timestamp !== "string" || !HOUR.test(e.timestamp)) return "bad timestamp";
  if (typeof e.install_id !== "string" || !UUID.test(e.install_id)) return "bad install_id";
  if (typeof e.tool_version !== "string" || !VERSION.test(e.tool_version)) return "bad tool_version";
  if (typeof e.python_version !== "string" || !PY_VERSION.test(e.python_version)) return "bad python_version";
  if (!OS_FAMILIES.includes(e.os_family)) return "bad os_family";
  if (!CI_PROVIDERS.includes(e.ci_provider)) return "bad ci_provider";
  if (!COMMANDS.includes(e.command)) return "bad command";
  if (typeof e.runtime_seconds !== "number" || !(e.runtime_seconds >= 0 && e.runtime_seconds < 86400)) return "bad runtime";
  if (!Number.isInteger(e.exit_code) || e.exit_code < 0 || e.exit_code > 255) return "bad exit_code";
  const fs = e.files_scanned;
  if (!fs || typeof fs !== "object" || Object.keys(fs).sort().join() !== "python,typescript_javascript"
      || !isCount(fs.python) || !isCount(fs.typescript_javascript)) return "bad files_scanned";
  const fr = e.findings_by_rule;
  if (!fr || typeof fr !== "object" || Array.isArray(fr)) return "bad findings_by_rule";
  const rules = Object.entries(fr);
  if (rules.length > 64 || !rules.every(([k, v]) => RULE_ID.test(k) && isCount(v))) return "bad findings_by_rule";
  if ("license_key_sha256" in e && (typeof e.license_key_sha256 !== "string" || !HEX16.test(e.license_key_sha256))) {
    return "bad license_key_sha256";
  }
  return null;
}

async function store(db, e) {
  const findings = Object.values(e.findings_by_rule).reduce((a, b) => a + b, 0);
  await db.prepare(
    `INSERT INTO events (received_day, event_hour, install_id, tool_version, python_version, os_family, ci_provider,
       command, runtime_seconds, files_python, files_ts, findings_total, findings_by_rule, exit_code, license_key_sha256)
     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15)`
  ).bind(
    new Date().toISOString().slice(0, 10), e.timestamp, e.install_id, e.tool_version, e.python_version, e.os_family,
    e.ci_provider, e.command, e.runtime_seconds, e.files_scanned.python, e.files_scanned.typescript_javascript,
    findings, JSON.stringify(e.findings_by_rule), e.exit_code, e.license_key_sha256 ?? null
  ).run();
}

const reply = (status, text) => new Response(text, { status, headers: { "content-type": "text/plain; charset=utf-8" } });

export default {
  async fetch(request, env) {
    const { pathname } = new URL(request.url);
    if (pathname === "/" && request.method === "GET") {
      return reply(200, "spendlint telemetry endpoint. What is collected: https://github.com/vernialabs/spendlint/blob/main/docs/telemetry.md\n");
    }
    if (pathname !== "/v1/events") return reply(404, "not found\n");
    if (request.method !== "POST") return reply(405, "method not allowed\n");
    if (!(request.headers.get("content-type") || "").startsWith("application/json")) return reply(415, "expected application/json\n");

    const body = await request.text();
    if (body.length > MAX_BODY) return reply(413, "too large\n");
    let event;
    try {
      event = JSON.parse(body);
    } catch {
      return reply(400, "invalid json\n");
    }
    const problem = validate(event);
    if (problem) return reply(400, problem + "\n");

    try {
      await store(env.DB, event);
    } catch {
      return reply(503, "storage unavailable\n"); // the client ignores failures; the event stays in its local log
    }
    return new Response(null, { status: 204 });
  },
};
