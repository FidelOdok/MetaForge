import { test } from "node:test";
import assert from "node:assert/strict";
import { render } from "ink-testing-library";
import { StepTrace } from "./StepTrace.js";

test("renders a successful tool call with a check", () => {
  const { lastFrame } = render(
    <StepTrace steps={[{ tool: "twin.get_node", arguments: { id: "x" } }]} />,
  );
  const frame = lastFrame() ?? "";
  assert.match(frame, /twin\.get_node/);
  assert.match(frame, /✓/);
});

test("marks a failed tool call with a cross", () => {
  const { lastFrame } = render(<StepTrace steps={[{ tool: "boom.tool", error: "nope" }]} />);
  assert.match(lastFrame() ?? "", /✗/);
});

test("renders nothing for no steps", () => {
  const { lastFrame } = render(<StepTrace steps={[]} />);
  assert.equal((lastFrame() ?? "").trim(), "");
});
