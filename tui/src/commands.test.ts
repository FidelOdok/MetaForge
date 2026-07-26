import { test } from "node:test";
import assert from "node:assert/strict";
import { parseArgs } from "./commands.js";

test("splits positionals from long and short flags", () => {
  const p = parseArgs(["runs", "get", "run_123", "--json"]);
  assert.deepEqual(p._, ["runs", "get", "run_123"]);
  assert.equal(p.flags.json, true);
});

test("long flag with a value", () => {
  const p = parseArgs(["runs", "create", "--goal", "build a bracket"]);
  assert.deepEqual(p._, ["runs", "create"]);
  assert.equal(p.flags.goal, "build a bracket");
});

test("short flag with a value (-m)", () => {
  const p = parseArgs(["chat", "-m", "hello there"]);
  assert.deepEqual(p._, ["chat"]);
  assert.equal(p.flags.m, "hello there");
});

test("boolean flag when no value follows", () => {
  const p = parseArgs(["projects", "--json"]);
  assert.equal(p.flags.json, true);
});

test("request-json value is kept intact", () => {
  const p = parseArgs(["runs", "create", "--request-json", '{"goal":"x","flow":"design_v1"}']);
  assert.equal(p.flags["request-json"], '{"goal":"x","flow":"design_v1"}');
});
