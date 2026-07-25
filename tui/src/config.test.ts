import { test } from "node:test";
import assert from "node:assert/strict";
import { configPath, loadConfig } from "./config.js";

test("configPath points at the shared ~/.forge/config.json", () => {
  const p = configPath();
  assert.ok(p.includes(".forge"));
  assert.ok(p.endsWith("config.json"));
});

test("loadConfig always yields a gateway_url (default or from file)", () => {
  const c = loadConfig();
  assert.equal(typeof c.gateway_url, "string");
  assert.ok(c.gateway_url.length > 0);
});
