import { test } from "node:test";
import assert from "node:assert/strict";
import { render } from "ink-testing-library";
import { StatusBar } from "./StatusBar.js";

test("StatusBar shows gateway, project, model and mode", () => {
  const { lastFrame } = render(
    <StatusBar
      gatewayUrl="http://gw:8000"
      health="healthy"
      model="gpt-4o"
      mode="ask"
      project="Quadruped"
    />,
  );
  const frame = lastFrame() ?? "";
  assert.match(frame, /gw:8000/);
  assert.match(frame, /healthy/);
  assert.match(frame, /Quadruped/);
  assert.match(frame, /gpt-4o/);
  assert.match(frame, /ask/);
});
