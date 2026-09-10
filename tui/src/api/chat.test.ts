import { test } from "node:test";
import assert from "node:assert/strict";
import { parseEvent } from "./chat.js";

test("parses message.delta", () => {
  assert.deepEqual(parseEvent('event: message.delta\ndata: {"delta":"hi"}'), {
    type: "message.delta",
    delta: "hi",
  });
});

test("parses agent.step with the step payload", () => {
  const ev = parseEvent('event: agent.step\ndata: {"step":{"tool":"twin.get_node"}}');
  assert.equal(ev?.type, "agent.step");
  assert.equal(ev?.type === "agent.step" ? ev.step.tool : "", "twin.get_node");
});

test("parses context.stats (unwraps the data envelope)", () => {
  const raw =
    'event: context.stats\ndata: {"data":{"window":200000,"used":4200,"available":195800,' +
    '"utilization":0.021,"components":[{"key":"system","label":"System prompt","tokens":92}],' +
    '"estimated":true},"thread_id":"t1"}';
  const ev = parseEvent(raw);
  assert.equal(ev?.type, "context.stats");
  if (ev?.type === "context.stats") {
    assert.equal(ev.stats.window, 200000);
    assert.equal(ev.stats.used, 4200);
    assert.equal(ev.stats.components[0].key, "system");
  }
});

test("parses agent.done and error", () => {
  assert.deepEqual(parseEvent("event: agent.done\ndata: {}"), { type: "agent.done" });
  assert.deepEqual(parseEvent('event: error\ndata: {"error":"boom"}'), {
    type: "error",
    error: "boom",
  });
});

test("returns null on missing data or malformed json", () => {
  assert.equal(parseEvent("event: message.delta"), null);
  assert.equal(parseEvent("event: error\ndata: {bad"), null);
});

test("unknown event falls through to 'other'", () => {
  const ev = parseEvent('event: agent.typing\ndata: {"agent_id":"a"}');
  assert.equal(ev?.type, "other");
});

// The gateway wraps payloads in a `data` envelope with thread_id/timestamp
// siblings — the real wire shape. Unwrapping it is what makes streamed chat
// render instead of showing "(no reply)".
test("unwraps the gateway's data envelope for message.delta", () => {
  const raw =
    'event: message.delta\ndata: {"data":{"delta":"Hello","agent_id":"agent"},"thread_id":"t","timestamp":"ts"}';
  assert.deepEqual(parseEvent(raw), { type: "message.delta", delta: "Hello" });
});

test("parses scope.changed for a project switch (MET-580, agent-triggered)", () => {
  const raw =
    'event: scope.changed\ndata: {"data":{"scope_kind":"project","scope_entity_id":"p1",' +
    '"project_name":"Monitor Build Demo"},"thread_id":"t"}';
  const ev = parseEvent(raw);
  assert.deepEqual(ev, {
    type: "scope.changed",
    scope: { scope_kind: "project", scope_entity_id: "p1", project_name: "Monitor Build Demo" },
  });
});

test("parses scope.changed for a detach (project_name null)", () => {
  const raw =
    'event: scope.changed\ndata: {"scope_kind":"assistant","scope_entity_id":"t1",' +
    '"project_name":null}';
  const ev = parseEvent(raw);
  assert.deepEqual(ev, {
    type: "scope.changed",
    scope: { scope_kind: "assistant", scope_entity_id: "t1", project_name: null },
  });
});

test("unwraps the envelope for agent.step and error", () => {
  const step = parseEvent(
    'event: agent.step\ndata: {"data":{"step":{"tool":"twin.get_node"}},"thread_id":"t"}',
  );
  assert.equal(step?.type === "agent.step" ? step.step.tool : "", "twin.get_node");
  const err = parseEvent('event: error\ndata: {"data":{"error":"boom"},"thread_id":"t"}');
  assert.deepEqual(err, { type: "error", error: "boom" });
});

// MET-610: the compiled binary runs under Bun, whose fetch kills any
// connection idle for 5 minutes. Both long-lived fetches must opt out with
// Bun's `timeout: false`, or a silent model call severs the stream/turn.
test("stream fetch disables the runtime idle timeout (MET-610)", async () => {
  const { streamThread } = await import("./chat.js");
  const orig = globalThis.fetch;
  let init: Record<string, unknown> | undefined;
  globalThis.fetch = (async (_url: unknown, i?: unknown) => {
    init = i as Record<string, unknown>;
    return {
      ok: true,
      body: new ReadableStream({
        start(c) {
          c.close();
        },
      }),
    } as unknown as Response;
  }) as typeof fetch;
  try {
    const events = [];
    for await (const ev of streamThread("http://x", "t1", new AbortController().signal)) {
      events.push(ev);
    }
    assert.deepEqual(events, []);
    assert.equal(init?.timeout, false);
  } finally {
    globalThis.fetch = orig;
  }
});
