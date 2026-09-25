import { test } from "node:test";
import assert from "node:assert/strict";
import {
  describeEmptyTurn,
  newTurnStats,
  summarizeProviderError,
  type TurnStats,
} from "./chat-diagnostics.js";

const stats = (over: Partial<TurnStats>): TurnStats => ({ ...newTurnStats(), ...over });

test("a turn with text has no cause", () => {
  assert.equal(describeEmptyTurn(stats({ events: 9, deltas: 9, chars: 42 })), null);
});

test("a stream error is reported with its message", () => {
  assert.equal(
    describeEmptyTurn(stats({ events: 1, errored: true, errorMsg: "boom" })),
    "stream error: boom",
  );
});

test("no events at all is distinct from an empty answer", () => {
  assert.match(describeEmptyTurn(stats({ events: 0 })) ?? "", /no stream events received/);
});

test("deltas-but-no-chars is flagged as a parse/envelope mismatch (the hidden bug)", () => {
  const reason = describeEmptyTurn(stats({ events: 9, deltas: 9, chars: 0 }));
  assert.match(reason ?? "", /9 delta event\(s\) but 0 characters/);
  assert.match(reason ?? "", /parse mismatch/);
});

test("events but no deltas means the agent produced no output", () => {
  assert.match(
    describeEmptyTurn(stats({ events: 2, deltas: 0, chars: 0 })) ?? "",
    /produced no output/,
  );
});

// FORGE-101: a provider 402/429/etc reaches the client as a raw multi-line
// dump (can include the provider's key-management URL and account user_id) —
// summarizeProviderError() shortens it to one line for on-screen display,
// while the caller keeps logging the untouched raw string to session.log.

test("summarizes an AllProvidersFailedError to a one-line, decoded reason", () => {
  const raw =
    "all providers failed for role 'generator': openrouter:openai/gpt-4o -> " +
    "Error code: 402 - {'error': {'message': 'This request requires more credits, " +
    "or fewer max_tokens. See https://openrouter.ai/settings/keys for your " +
    "account details.', 'code': 402}, 'user_id': 'user_9f2a1c7e8b3d4a5f'}";
  assert.equal(
    summarizeProviderError(raw),
    "provider openrouter · openai/gpt-4o: out of credits (402), see ~/.forge/logs/session.log",
  );
});

test("picks the LAST attempted provider when several were tried", () => {
  const raw =
    "all providers failed for role 'generator': " +
    "anthropic:claude-sonnet-5 -> Error code: 429 - rate limited; " +
    "openrouter:openai/gpt-4o -> Error code: 402 - out of credits";
  assert.equal(
    summarizeProviderError(raw),
    "provider openrouter · openai/gpt-4o: out of credits (402), see ~/.forge/logs/session.log",
  );
});

test("matches the exact test_chat_error_surfacing.py fixture (a 400)", () => {
  const raw =
    "all providers failed for role 'generator': openrouter:openai/gpt-4o-mini -> " +
    "Error code: 400 - Invalid 'tools': array too long. Expected an array with " +
    "maximum length 128, but got an array with length 130 instead.";
  assert.equal(
    summarizeProviderError(raw),
    "provider openrouter · openai/gpt-4o-mini: bad request (400), see ~/.forge/logs/session.log",
  );
});

test("falls back to a truncated one-liner for an unrecognized shape", () => {
  const raw = "x".repeat(300);
  const summary = summarizeProviderError(raw);
  assert.ok(summary.length < raw.length, "must actually shorten a long unstructured error");
  assert.match(summary, /see ~\/\.forge\/logs\/session\.log$/);
});

test("leaves a short, already-clean message untouched", () => {
  assert.equal(summarizeProviderError("network timeout"), "network timeout");
});
