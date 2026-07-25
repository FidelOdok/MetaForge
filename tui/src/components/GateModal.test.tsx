import { test } from "node:test";
import assert from "node:assert/strict";
import { render } from "ink-testing-library";
import { GateModal } from "./GateModal.js";

test("shows the gate reason, ready state and actions", () => {
  const { lastFrame } = render(
    <GateModal reason="Design review complete. Deliverables present: ['cad_model']; missing: none" />,
  );
  const frame = lastFrame() ?? "";
  assert.match(frame, /approval required/);
  assert.match(frame, /deliverables present/);
  assert.match(frame, /\[a\] approve/);
  assert.match(frame, /\[x\] reject/);
});

test("flags missing deliverables", () => {
  const { lastFrame } = render(<GateModal reason="missing: ['cad_model']" />);
  assert.match(lastFrame() ?? "", /deliverables missing/);
});

test("shows a submitting state without actions", () => {
  const { lastFrame } = render(<GateModal reason="x" busy />);
  const frame = lastFrame() ?? "";
  assert.match(frame, /submitting/);
  assert.doesNotMatch(frame, /\[a\] approve/);
});
