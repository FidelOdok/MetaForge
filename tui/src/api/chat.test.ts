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

test("unwraps the envelope for agent.step and error", () => {
  const step = parseEvent(
    'event: agent.step\ndata: {"data":{"step":{"tool":"twin.get_node"}},"thread_id":"t"}',
  );
  assert.equal(step?.type === "agent.step" ? step.step.tool : "", "twin.get_node");
  const err = parseEvent('event: error\ndata: {"data":{"error":"boom"},"thread_id":"t"}');
  assert.deepEqual(err, { type: "error", error: "boom" });
});
