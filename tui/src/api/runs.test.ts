import { test } from "node:test";
import assert from "node:assert/strict";
import { isTerminal } from "./runs.js";

test("terminal statuses are terminal", () => {
  for (const s of ["completed", "failed", "rejected", "canceled"]) {
    assert.equal(isTerminal(s), true, s);
  }
});

test("in-flight statuses are not terminal", () => {
  for (const s of ["running", "awaiting_approval", "queued"]) {
    assert.equal(isTerminal(s), false, s);
  }
  assert.equal(isTerminal(undefined), false);
});
