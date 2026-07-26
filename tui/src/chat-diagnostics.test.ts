import { test } from "node:test";
import assert from "node:assert/strict";
import { describeEmptyTurn, newTurnStats, type TurnStats } from "./chat-diagnostics.js";

const stats = (over: Partial<TurnStats>): TurnStats => ({ ...newTurnStats(), ...over });

test("a turn with text has no cause", () => {
  assert.equal(describeEmptyTurn(stats({ events: 9, deltas: 9, chars: 42 })), null);
});

test("a stream error is reported with its message", () => {
  assert.equal(
    describeEmptyTurn(stats({ events: 1, errored: true, errorMsg: "boom" })),
    "stream error: boom",
  );
});

test("no events at all is distinct from an empty answer", () => {
  assert.match(describeEmptyTurn(stats({ events: 0 })) ?? "", /no stream events received/);
});

test("deltas-but-no-chars is flagged as a parse/envelope mismatch (the hidden bug)", () => {
  const reason = describeEmptyTurn(stats({ events: 9, deltas: 9, chars: 0 }));
  assert.match(reason ?? "", /9 delta event\(s\) but 0 characters/);
  assert.match(reason ?? "", /parse mismatch/);
});

test("events but no deltas means the agent produced no output", () => {
  assert.match(
    describeEmptyTurn(stats({ events: 2, deltas: 0, chars: 0 })) ?? "",
    /produced no output/,
  );
});
