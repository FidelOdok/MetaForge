import { test } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { render } from "ink-testing-library";
import { Chat } from "./Chat.js";
import { TAGLINE } from "../banner.js";
import type { GatewayClient } from "../api/client.js";
import type { ChatMessage, UseChat } from "../hooks/useChat.js";

function fakeClient(): GatewayClient {
  return { baseUrl: () => "http://localhost:8000" } as unknown as GatewayClient;
}

/** Chat is presentational now — App owns the thread and passes it in. This
 *  builds a UseChat snapshot for a given status / transcript. */
function chatState(overrides: Partial<UseChat> = {}): UseChat {
  return {
    status: "connecting",
    error: null,
    messages: [],
    pending: null,
    contextStats: null,
    send: () => {},
    ...overrides,
  };
}

test("launch layout shows the Welcome splash at the top when empty", () => {
  const { lastFrame, unmount } = render(
    React.createElement(Chat, { client: fakeClient(), chat: chatState() }),
  );
  const frame = lastFrame() ?? "";
  // Welcome renders (its tagline is present) and sits at the top of the launch
  // screen, above the bottom-pinned input.
  assert.ok(frame.includes("intent → manufacturable hardware"), `tagline missing:\n${frame}`);
  assert.ok(TAGLINE.length > 0);
  unmount();
});

test("launch layout shows the input box below the Welcome", () => {
  const { lastFrame, unmount } = render(
    React.createElement(Chat, { client: fakeClient(), chat: chatState() }),
  );
  const frame = lastFrame() ?? "";
  const taglineAt = frame.indexOf("intent → manufacturable hardware");
  const promptAt = frame.lastIndexOf("connecting…");
  assert.ok(promptAt >= 0, `input placeholder missing:\n${frame}`);
  assert.ok(taglineAt >= 0 && promptAt > taglineAt, `input not below welcome:\n${frame}`);
  unmount();
});

test("transcript layout renders finalized turns once a session has started", () => {
  const messages: ChatMessage[] = [
    { role: "user", text: "hello there" },
    { role: "assistant", text: "hi — how can I help?" },
  ];
  const { lastFrame, unmount } = render(
    React.createElement(Chat, {
      client: fakeClient(),
      chat: chatState({ status: "idle", messages }),
    }),
  );
  const frame = lastFrame() ?? "";
  // Both turns are present (rendered via <Static>) and the idle input prompt
  // follows them — no "connecting…" launch placeholder.
  assert.ok(frame.includes("hello there"), `user turn missing:\n${frame}`);
  assert.ok(frame.includes("hi — how can I help?"), `assistant turn missing:\n${frame}`);
  assert.ok(frame.includes("message  (/model"), `input prompt missing:\n${frame}`);
  unmount();
});
