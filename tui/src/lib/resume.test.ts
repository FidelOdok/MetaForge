import { strict as assert } from "node:assert";
import { test } from "node:test";
import { backfillMessages, describeThread, pickerCandidates, relativeTime } from "./resume.js";
import { decideInvocation } from "./invocation.js";

test("backfill maps user/agent rows and drops system + empty", () => {
  const out = backfillMessages([
    { actor_kind: "user", content: "design a bracket" },
    { actor_kind: "agent", content: "What loads should it carry?" },
    { actor_kind: "system", content: "Harness error: boom" },
    { actor_kind: "agent", content: "" },
  ]);
  assert.deepEqual(out, [
    { role: "user", text: "design a bracket" },
    { role: "assistant", text: "What loads should it carry?" },
  ]);
});

test("relativeTime buckets", () => {
  const now = Date.parse("2026-08-06T12:00:00Z");
  assert.equal(relativeTime("2026-08-06T11:59:30Z", now), "30s ago");
  assert.equal(relativeTime("2026-08-06T11:30:00Z", now), "30m ago");
  assert.equal(relativeTime("2026-08-06T09:00:00Z", now), "3h ago");
  assert.equal(relativeTime("2026-08-01T12:00:00Z", now), "5d ago");
  assert.equal(relativeTime(undefined, now), "");
  assert.equal(relativeTime("not-a-date", now), "");
});

test("pickerCandidates filters archived, empty, and the current thread", () => {
  const t = (id: string, over: object = {}) => ({
    id,
    scope_kind: "assistant",
    scope_entity_id: "e",
    title: id,
    archived: false,
    message_count: 4,
    ...over,
  });
  const out = pickerCandidates(
    [t("current"), t("archived", { archived: true }), t("empty", { message_count: 0 }), t("ok")],
    "current",
  );
  assert.deepEqual(
    out.map((x) => x.id),
    ["ok"],
  );
});

test("describeThread renders title, scope, count, and age", () => {
  const now = Date.parse("2026-08-06T12:00:00Z");
  const line = describeThread(
    {
      id: "x",
      scope_kind: "project",
      scope_entity_id: "abcdef12-3456",
      title: "Monitor build",
      archived: false,
      message_count: 9,
      last_message_at: "2026-08-06T10:00:00Z",
    },
    now,
  );
  assert.equal(line, "Monitor build · project abcdef12 · 9 msg · 2h ago");
});

test("--continue / -c launches the TUI with continueLatest", () => {
  assert.deepEqual(decideInvocation(["--continue"]), {
    mode: "tui",
    debug: false,
    continueLatest: true,
  });
  assert.equal(decideInvocation(["-c"]).continueLatest, true);
  assert.equal(decideInvocation([]).continueLatest, undefined);
});
