import { strict as assert } from "node:assert";
import { test } from "node:test";
import { messageHeight, transcriptHeight, wrappedLines } from "./transcript-height.js";

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
