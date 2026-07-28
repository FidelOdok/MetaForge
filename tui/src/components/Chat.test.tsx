import { test } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { render } from "ink-testing-library";
import { Chat } from "./Chat.js";
import { TAGLINE } from "../banner.js";
import type { GatewayClient } from "../api/client.js";

/** A client that never resolves createThread, so useChat stays in "connecting"
 * and no network/SSE is attempted — the render stays a stable first frame. */
function fakeClient(): GatewayClient {
  return {
    baseUrl: () => "http://localhost:8000",
    createThread: () => new Promise(() => {}),
  } as unknown as GatewayClient;
}

test("chat mounts and shows the Welcome splash at the top", () => {
  const { lastFrame, unmount } = render(React.createElement(Chat, { client: fakeClient() }));
  const frame = lastFrame() ?? "";
  // Welcome renders (its tagline is present) — it's the first <Static> row, so
  // it sits at the top of the transcript above everything else.
  assert.ok(frame.includes("intent → manufacturable hardware"), `tagline missing:\n${frame}`);
  assert.ok(TAGLINE.length > 0);
  unmount();
});

test("chat shows the input box below the transcript", () => {
  const { lastFrame, unmount } = render(React.createElement(Chat, { client: fakeClient() }));
  const frame = lastFrame() ?? "";
  const taglineAt = frame.indexOf("intent → manufacturable hardware");
  const promptAt = frame.lastIndexOf("connecting…");
  // The connecting placeholder (input box) appears, and after the Welcome — i.e.
  // Welcome is at the top, input pinned below.
  assert.ok(promptAt >= 0, `input placeholder missing:\n${frame}`);
  assert.ok(taglineAt >= 0 && promptAt > taglineAt, `input not below welcome:\n${frame}`);
  unmount();
});

test("reports started=false for an empty session (launch layout)", async () => {
  const seen: boolean[] = [];
  const { unmount } = render(
    React.createElement(Chat, { client: fakeClient(), onStartedChange: (s) => seen.push(s) }),
  );
  // Let the post-commit effect flush.
  await new Promise((r) => setImmediate(r));
  // With no turns yet, Chat renders the full-height launch layout and tells the
  // parent it hasn't started, so App keeps the fixed height that pins the input
  // to the bottom of the terminal.
  assert.deepEqual(seen, [false], `expected a single started=false, got ${JSON.stringify(seen)}`);
  unmount();
});
