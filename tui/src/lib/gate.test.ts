import { test } from "node:test";
import assert from "node:assert/strict";
import { gateReady, statusColor } from "./gate.js";

test("gateReady is true when the reason reports no missing deliverables", () => {
  assert.equal(gateReady("Deliverables present: ['cad_model']; missing: none"), true);
  assert.equal(gateReady("... missing:none"), true);
});

test("gateReady is false when deliverables are missing", () => {
  assert.equal(gateReady("present: none; missing: ['cad_model']"), false);
  assert.equal(gateReady("no readiness info here"), false);
});

test("statusColor maps known statuses and defaults", () => {
  assert.equal(statusColor("completed"), "green");
  assert.equal(statusColor("awaiting_approval"), "yellow");
  assert.equal(statusColor("failed"), "red");
  assert.equal(statusColor("mystery"), "white");
  assert.equal(statusColor(undefined), "white");
});
