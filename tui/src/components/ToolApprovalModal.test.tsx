import { test } from "node:test";
import assert from "node:assert/strict";
import { render } from "ink-testing-library";
import { ToolApprovalModal } from "./ToolApprovalModal.js";

test("shows the tool name and a compact arguments preview", () => {
  const { lastFrame } = render(
    <ToolApprovalModal
      tool="mcp_twin_commit_geometry"
      arguments={{ name: "leg bracket", project_id: "p-1" }}
    />,
  );
  const frame = lastFrame() ?? "";
  assert.match(frame, /Approval required/);
  assert.match(frame, /mcp_twin_commit_geometry/);
  assert.match(frame, /leg bracket/);
  assert.match(frame, /\[a\] approve/);
  assert.match(frame, /\[x\] reject/);
});

test("shows a submitting state without actions", () => {
  const { lastFrame } = render(
    <ToolApprovalModal tool="twin.record_decision" arguments={{}} busy />,
  );
  const frame = lastFrame() ?? "";
  assert.match(frame, /submitting/);
  assert.doesNotMatch(frame, /\[a\] approve/);
});

test("truncates a large arguments object instead of overflowing", () => {
  const args = Object.fromEntries(Array.from({ length: 20 }, (_, i) => [`field_${i}`, i]));
  const { lastFrame } = render(<ToolApprovalModal tool="twin.record_decision" arguments={args} />);
  assert.match(lastFrame() ?? "", /more line\(s\)/);
});
