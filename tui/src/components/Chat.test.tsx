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
    pendingApproval: null,
    approvalBusy: false,
    resolveApproval: () => {},
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

test("FORGE-33: a paused tool call renders the approval modal", () => {
  const { lastFrame, unmount } = render(
    React.createElement(Chat, {
      client: fakeClient(),
      chat: chatState({
        status: "thinking",
        pendingApproval: {
          run_id: "run_1",
          tool: "mcp_twin_commit_geometry",
          arguments: { name: "leg bracket" },
        },
      }),
    }),
  );
  const frame = lastFrame() ?? "";
  assert.match(frame, /Approval required/);
  assert.match(frame, /mcp_twin_commit_geometry/);
  assert.match(frame, /\[a\] approve/);
  unmount();
});

test("FORGE-33: pressing 'a' resolves the pending approval", async () => {
  const decisions: string[] = [];
  const { stdin, unmount } = render(
    React.createElement(Chat, {
      client: fakeClient(),
      chat: chatState({
        status: "thinking",
        pendingApproval: {
          run_id: "run_1",
          tool: "twin.record_decision",
          arguments: {},
        },
        resolveApproval: (decision: "approve" | "reject") => decisions.push(decision),
      }),
    }),
  );
  stdin.write("a");
  await tick();
  assert.deepEqual(decisions, ["approve"]);
  unmount();
});

test("FORGE-33: pressing 'x' rejects, and a-key input isn't sent as a chat message", async () => {
  const decisions: string[] = [];
  const sent: string[] = [];
  const { stdin, unmount } = render(
    React.createElement(Chat, {
      client: fakeClient(),
      chat: chatState({
        status: "thinking",
        pendingApproval: {
          run_id: "run_1",
          tool: "twin.record_decision",
          arguments: {},
        },
        resolveApproval: (decision: "approve" | "reject") => decisions.push(decision),
        send: (c: string) => sent.push(c),
      }),
    }),
  );
  stdin.write("x");
  await tick();
  assert.deepEqual(decisions, ["reject"]);
  assert.deepEqual(sent, []);
  unmount();
});

test("FORGE-95: the approval keypress doesn't leak into the input box afterward", async () => {
  // Live repro: "message sent as aaRecord the arm requirements…" -- the a/x
  // keystroke that resolves an approval was ALSO landing in TextInput's own
  // buffer (a second, independently-active input handler), so it prefixed
  // whatever the user typed next.
  const decisions: string[] = [];
  const sent: string[] = [];
  const props = (pendingApproval: UseChat["pendingApproval"]) => ({
    client: fakeClient(),
    chat: chatState({
      status: "idle" as const,
      pendingApproval,
      resolveApproval: (decision: "approve" | "reject") => decisions.push(decision),
      send: (c: string) => sent.push(c),
    }),
  });
  const { stdin, rerender, unmount } = render(
    React.createElement(Chat, props({ run_id: "run_1", tool: "twin.record_decision", arguments: {} })),
  );
  stdin.write("a");
  await tick();
  assert.deepEqual(decisions, ["approve"]);

  // The modal closes once the approval resolves -- exactly what App does on
  // a real resolveApproval() round-trip.
  rerender(React.createElement(Chat, props(null)));
  await tick();

  stdin.write("Record the arm requirements");
  await tick();
  stdin.write("\r");
  await tick();
  assert.deepEqual(sent, ["Record the arm requirements"]);
  unmount();
});
