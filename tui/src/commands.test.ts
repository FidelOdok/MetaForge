import { test } from "node:test";
import assert from "node:assert/strict";
import { parseArgs, resolveLoginMethod } from "./commands.js";

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

test("--flag=value form", () => {
  const p = parseArgs(["chat", "--project=Monitor Build Demo", "--json"]);
  assert.deepEqual(p._, ["chat"]);
  assert.equal(p.flags.project, "Monitor Build Demo");
  assert.equal(p.flags.json, true);
});

test("request-json value is kept intact", () => {
  const p = parseArgs(["runs", "create", "--request-json", '{"goal":"x","flow":"design_v1"}']);
  assert.equal(p.flags["request-json"], '{"goal":"x","flow":"design_v1"}');
});

test("auth login flags parse (provider/method/api-key)", () => {
  const p = parseArgs(["auth", "login", "--provider", "openai", "--method", "api-key", "--api-key", "sk-x"]);
  assert.deepEqual(p._, ["auth", "login"]);
  assert.equal(p.flags.provider, "openai");
  assert.equal(p.flags.method, "api-key");
  assert.equal(p.flags["api-key"], "sk-x");
});

test("auth use positionals + model flag", () => {
  const p = parseArgs(["auth", "use", "openai-codex", "--model", "gpt-5-codex"]);
  assert.deepEqual(p._, ["auth", "use", "openai-codex"]);
  assert.equal(p.flags.model, "gpt-5-codex");
});

// Regression: the registry's CODEX family constant is literally "openai-codex"
// (never the bare string "codex") — checking for "codex" silently defaulted
// every `auth login --provider openai-codex` to the api-key method, which sat
// on a hidden prompt instead of starting OAuth (looked like a hang).
test("resolveLoginMethod defaults openai-codex to oauth, by family or by id", () => {
  assert.equal(resolveLoginMethod(undefined, "openai-codex", "openai-codex"), "oauth");
  assert.equal(resolveLoginMethod(undefined, undefined, "openai-codex"), "oauth");
  assert.equal(resolveLoginMethod(undefined, "codex", "some-other-id"), "api-key");
});

test("resolveLoginMethod defaults non-codex providers to api-key", () => {
  assert.equal(resolveLoginMethod(undefined, "openai", "openai"), "api-key");
  assert.equal(resolveLoginMethod(undefined, "anthropic", "anthropic"), "api-key");
});

test("resolveLoginMethod: an explicit --method always wins", () => {
  assert.equal(resolveLoginMethod("api-key", "openai-codex", "openai-codex"), "api-key");
  assert.equal(resolveLoginMethod("oauth", "openai", "openai"), "oauth");
});
