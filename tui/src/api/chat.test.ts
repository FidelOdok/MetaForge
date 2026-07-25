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
