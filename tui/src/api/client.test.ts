import { test } from "node:test";
import assert from "node:assert/strict";
import { GatewayClient } from "./client.js";
import type { ForgeConfig } from "../config.js";

// MET-610: the compiled binary runs under Bun, whose fetch aborts with "The
// operation timed out." after 5 idle minutes — exactly what a synchronous
// turn POST looks like (no response bytes until the turn settles). The
// long-turn send must carry Bun's `timeout: false` opt-out; the undici
// dispatcher alone only covers Node.
test("sendMessage disables the runtime idle timeout (MET-610)", async () => {
  const orig = globalThis.fetch;
  let init: Record<string, unknown> | undefined;
  globalThis.fetch = (async (_url: unknown, i?: unknown) => {
    init = i as Record<string, unknown>;
    return { ok: true, json: async () => ({}) } as unknown as Response;
  }) as typeof fetch;
  try {
    const client = new GatewayClient({ gateway_url: "http://x" } as ForgeConfig);
    await client.sendMessage("t1", "hello");
    assert.equal(init?.timeout, false);
    assert.ok(init?.dispatcher, "keeps the undici dispatcher for Node runs");
    assert.ok(init?.signal, "keeps the hard-ceiling abort signal");
  } finally {
    globalThis.fetch = orig;
  }
});
