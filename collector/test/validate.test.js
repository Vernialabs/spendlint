import { test } from "node:test";
import assert from "node:assert/strict";
import { validate } from "../src/index.js";

const good = () => ({
  schema_version: 1, event: "run", timestamp: "2026-10-24T15:00:00Z",
  install_id: "0b6f8f53-3c1a-4d55-9a57-2f1d7c1e4a10", tool_version: "0.1.2", python_version: "3.12",
  os_family: "windows", ci_provider: "none", command: "scan", runtime_seconds: 0.4,
  files_scanned: { python: 12, typescript_javascript: 3 }, findings_by_rule: { "R-CACHE-001": 2 }, exit_code: 0,
});

test("accepts a real event", () => assert.equal(validate(good()), null));

test("accepts an optional license hash", () => assert.equal(validate({ ...good(), license_key_sha256: "0123456789abcdef" }), null));

test("rejects extra keys, such as a file path", () => assert.equal(validate({ ...good(), path: "/home/me/repo" }), "unknown keys"));

test("rejects missing keys", () => {
  const e = good();
  delete e.install_id;
  assert.equal(validate(e), "missing keys");
});

test("rejects free text hidden in a value", () => {
  assert.notEqual(validate({ ...good(), os_family: "linux /home/me" }), null);
  assert.notEqual(validate({ ...good(), tool_version: "0.1.2 my-secret-repo-name" }), null);
  assert.notEqual(validate({ ...good(), findings_by_rule: { "src/app.py": 1 } }), null);
  assert.notEqual(validate({ ...good(), files_scanned: { python: 1, typescript_javascript: 1, go: 4 } }), null);
});

test("rejects timestamps finer than an hour", () => assert.equal(validate({ ...good(), timestamp: "2026-10-24T15:42:07Z" }), "bad timestamp"));

test("rejects non-objects", () => {
  assert.equal(validate([]), "not an object");
  assert.equal(validate(null), "not an object");
});
