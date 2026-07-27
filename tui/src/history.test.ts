import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const dir = mkdtempSync(join(tmpdir(), "forge-hist-"));
process.env.FORGE_HISTORY_FILE = join(dir, "history");

const { loadHistory, appendHistory } = await import("./history.js");

test("empty history loads as []", () => {
  assert.deepEqual(loadHistory(), []);
});

test("appended prompts round-trip, oldest first", () => {
  appendHistory("first message");
  appendHistory("/model gpt-5.5");
  appendHistory("second message");
  assert.deepEqual(loadHistory(), ["first message", "/model gpt-5.5", "second message"]);
});

test("blank entries are skipped", () => {
  const before = loadHistory().length;
  appendHistory("   ");
  appendHistory("");
  assert.equal(loadHistory().length, before);
});
