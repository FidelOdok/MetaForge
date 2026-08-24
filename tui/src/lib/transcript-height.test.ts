import { strict as assert } from "node:assert";
import { test } from "node:test";
import { messageHeight, pendingHeight, transcriptHeight, wrappedLines } from "./transcript-height.js";

test("wrappedLines: newlines + wrapping + empty lines count", () => {
  assert.equal(wrappedLines("short", 80), 1);
  assert.equal(wrappedLines("a".repeat(200), 80), 3);
  assert.equal(wrappedLines("a\n\nb", 80), 3);
});

test("user message: margin + header + text", () => {
  assert.equal(messageHeight({ role: "user", text: "hi" }, 82), 3);
});

test("system note: margin + text", () => {
  assert.equal(messageHeight({ role: "system", text: "— resumed —" }, 82), 2);
});

test("assistant with steps: margin + thinking header + steps + header + text", () => {
  const m = {
    role: "assistant" as const,
    text: "done",
    steps: [{ tool: "echo", arguments: {} }, { thought: "hmm" }],
  };
  // 1 margin + 1 "· thinking" + 2 step rows (wide terminal) + 1 "◆" + 1 text
  assert.equal(messageHeight(m, 200), 6);
});

test("assistant no-reply renders one placeholder line", () => {
  assert.equal(messageHeight({ role: "assistant", text: "" }, 82), 3);
});

test("transcript sums messages", () => {
  const msgs = [
    { role: "user" as const, text: "hi" },
    { role: "assistant" as const, text: "hello" },
  ];
  assert.equal(transcriptHeight(msgs, 82), 3 + 3);
});

test("pendingHeight: null pending is zero", () => {
  assert.equal(pendingHeight(null, 82, true), 0);
});

test("pendingHeight: idle spinner while no trace or text yet", () => {
  // margin + spinner line
  assert.equal(pendingHeight({ text: "", steps: [] }, 82, true), 2);
});

test("pendingHeight: not busy and nothing yet is just the margin", () => {
  assert.equal(pendingHeight({ text: "", steps: [] }, 82, false), 1);
});

test("pendingHeight: grows with the tool trace, mirroring messageHeight's steps math", () => {
  const pending = { text: "", steps: [{ tool: "echo" }, { thought: "hmm" }] };
  // margin + "· thinking" + 2 step rows (wide terminal) + spinner (still busy, no text)
  assert.equal(pendingHeight(pending, 200, true), 5);
});

test("pendingHeight: streamed text replaces the spinner with a header + wrapped text", () => {
  const pending = { text: "hello there", steps: [] };
  assert.equal(pendingHeight(pending, 82, true), 1 + 1 + 1);
});

test("pendingHeight: thinking preview wraps like any other text", () => {
  const pending = { text: "", steps: [], thinking: "a".repeat(200) };
  // margin + "· thinking" + wrapped thinking (narrower: marginLeft) + spinner
  assert.equal(
    pendingHeight(pending, 82, true),
    1 + 1 + wrappedLines("a".repeat(200), 82 - 2 - 1) + 1,
  );
});
