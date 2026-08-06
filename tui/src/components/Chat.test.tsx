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

/** Let Ink apply the keystrokes it just received and repaint. */
const tick = (ms = 50) => new Promise((r) => setTimeout(r, ms));

/** Chat is presentational now — App owns the thread and passes it in. This
 *  builds a UseChat snapshot for a given status / transcript. */
function chatState(overrides: Partial<UseChat> = {}): UseChat {
  return {
    status: "connecting",
    error: null,
    messages: [],
    pending: null,
    contextStats: null,
    threadScope: null,
    send: () => {},
    resume: () => {},
    threadId: null,
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

test("a system notice renders as a dim line, not an assistant turn", () => {
  const messages: ChatMessage[] = [
    { role: "system", text: "— project → Monitor Build Demo · new thread —" },
  ];
  const { lastFrame, unmount } = render(
    React.createElement(Chat, {
      client: fakeClient(),
      chat: chatState({ status: "idle", messages }),
    }),
  );
  const frame = lastFrame() ?? "";
  assert.ok(frame.includes("project → Monitor Build Demo"), `notice missing:\n${frame}`);
  assert.ok(!frame.includes("◆ assistant"), `notice attributed to the agent:\n${frame}`);
  unmount();
});

test("/project hands the argument to the resolver and shows its answer", async () => {
  const seen: string[] = [];
  const { lastFrame, stdin, unmount } = render(
    React.createElement(Chat, {
      client: fakeClient(),
      chat: chatState({ status: "idle" }),
      onProjectChange: async (arg: string) => {
        seen.push(arg);
        return "project → Monitor Build Demo — starting a new thread in it";
      },
    }),
  );
  stdin.write("/project monitor");
  await tick();
  stdin.write("\r");
  // Resolution is async (it hits the gateway), so the notice lands a tick later.
  await tick();
  assert.deepEqual(seen, ["monitor"]);
  assert.ok(
    (lastFrame() ?? "").includes("project → Monitor Build Demo"),
    `notice missing:\n${lastFrame()}`,
  );
  unmount();
});

test("/project is not sent to the agent as a message", async () => {
  const sent: string[] = [];
  const { stdin, unmount } = render(
    React.createElement(Chat, {
      client: fakeClient(),
      chat: chatState({ status: "idle", send: (c: string) => sent.push(c) }),
      onProjectChange: async () => "project: none",
    }),
  );
  stdin.write("/project");
  await tick();
  stdin.write("\r");
  await tick();
  assert.deepEqual(sent, []);
  unmount();
});
