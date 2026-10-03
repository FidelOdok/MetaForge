import { test } from "node:test";
import assert from "node:assert/strict";
import { approvalHeaders, type ApprovalItem } from "./api/client.js";
import {
  EXIT_AUTH,
  EXIT_CONFLICT,
  EXIT_INVALID,
  EXIT_NOT_FOUND,
  approvalExitCode,
  formatApproval,
  runCommand,
  validateDecision,
} from "./commands.js";

function item(over: Partial<ApprovalItem> = {}): ApprovalItem {
  return {
    id: "gate:run_1",
    kind: "gate",
    status: "pending",
    title: "Design gate",
    summary: "s",
    project_id: "p1",
    created_at: "2026-10-03T00:00:00Z",
    deadline: null,
    route: "dashboard",
    requested_by: null,
    reason_held: null,
    findings: [{ kind: "ungrounded", severity: "error", message: "no source" }],
    allowed_decisions: ["approve", "reject", "retry", "rework"],
    rework_targets: ["design"],
    reason_required_for: ["reject", "retry", "rework"],
    decidable: true,
    not_decidable_reason: null,
    detail: { run_id: "run_1", phase: "mechanical" },
    decision: null,
    ...over,
  };
}

test("approvalHeaders: cli surface by default, bearer when configured", () => {
  assert.deepEqual(approvalHeaders({}), { "X-MetaForge-Surface": "cli" });
  assert.equal(approvalHeaders({ METAFORGE_AUTH_TOKEN: "t" }).Authorization, "Bearer t");
});

test("approvalHeaders: agent surface needs both env vars", () => {
  assert.equal(approvalHeaders({ METAFORGE_APPROVAL_AGENT: "bot" })["X-MetaForge-Surface"], "cli");
  const h = approvalHeaders({ METAFORGE_APPROVAL_AGENT: "bot", METAFORGE_APPROVAL_ON_BEHALF_OF: "alice" });
  assert.equal(h["X-MetaForge-Surface"], "agent");
  assert.equal(h["X-MetaForge-On-Behalf-Of"], "alice");
  assert.equal(h["X-MetaForge-Agent"], "bot");
});

test("approvalExitCode maps statuses", () => {
  assert.equal(approvalExitCode(404), EXIT_NOT_FOUND);
  assert.equal(approvalExitCode(409), EXIT_CONFLICT);
  assert.equal(approvalExitCode(422), EXIT_INVALID);
  assert.equal(approvalExitCode(401), EXIT_AUTH);
  assert.equal(approvalExitCode(500), 1);
  assert.equal(approvalExitCode(undefined), 1);
});

test("validateDecision enforces allowed, reason, and rework targets", () => {
  assert.equal(validateDecision(item(), "approve", undefined, undefined), null);
  assert.match(validateDecision(item(), "reject", undefined, undefined) ?? "", /requires --reason/);
  assert.match(validateDecision(item({ allowed_decisions: ["approve"] }), "retry", "x", undefined) ?? "", /not allowed/);
  assert.match(validateDecision(item(), "rework", "x", undefined) ?? "", /requires --to/);
  assert.match(validateDecision(item(), "rework", "x", "nope") ?? "", /not a rework target/);
  assert.equal(validateDecision(item(), "rework", "x", "design"), null);
});

test("formatApproval shows findings, decisions and targets", () => {
  const text = formatApproval(item());
  for (const n of ["no source", "approve, reject, retry, rework", "design", "mechanical"]) {
    assert.ok(text.includes(n), n);
  }
});

type Call = { url: string; method: string; headers: Record<string, string>; body?: string };

async function withFetch(
  responder: (c: Call) => { status: number; body: unknown },
  run: (calls: Call[]) => Promise<void>,
): Promise<void> {
  const orig = globalThis.fetch;
  const calls: Call[] = [];
  globalThis.fetch = (async (url: unknown, init?: RequestInit) => {
    const c: Call = {
      url: String(url),
      method: init?.method ?? "GET",
      headers: (init?.headers ?? {}) as Record<string, string>,
      body: init?.body as string | undefined,
    };
    calls.push(c);
    const r = responder(c);
    return new Response(JSON.stringify(r.body), { status: r.status });
  }) as typeof fetch;
  try {
    await run(calls);
  } finally {
    globalThis.fetch = orig;
  }
}

test("approvals approve posts to /decision with the cli surface header", async () => {
  await withFetch(
    (c) => ({ status: 200, body: c.method === "GET" ? item() : item({ status: "approved" }) }),
    async (calls) => {
      const code = await runCommand(["approvals", "approve", "gate:run_1", "--gateway", "http://gw", "--json"]);
      assert.equal(code, 0);
      assert.equal(calls[1]!.url, "http://gw/v1/approvals/gate:run_1/decision");
      assert.equal(calls[1]!.headers["X-MetaForge-Surface"], "cli");
      assert.deepEqual(JSON.parse(calls[1]!.body!), { decision: "approve" });
    },
  );
});

test("approvals reject without --reason fails locally with no POST", async () => {
  await withFetch(
    () => ({ status: 200, body: item() }),
    async (calls) => {
      const code = await runCommand(["approvals", "reject", "gate:run_1", "--gateway", "http://gw"]);
      assert.equal(code, EXIT_INVALID);
      assert.equal(calls.filter((c) => c.method === "POST").length, 0);
    },
  );
});

test("approvals rework sends to_phase", async () => {
  await withFetch(
    (c) => ({ status: 200, body: c.method === "GET" ? item() : item({ status: "reworked" }) }),
    async (calls) => {
      const code = await runCommand([
        "approvals", "rework", "gate:run_1", "--to", "design", "--reason", "redo", "--gateway", "http://gw", "--json",
      ]);
      assert.equal(code, 0);
      assert.deepEqual(JSON.parse(calls[1]!.body!), { decision: "rework", reason: "redo", to_phase: "design" });
    },
  );
});

test("approvals list builds the query and 404/409 map to exit codes", async () => {
  await withFetch(
    () => ({ status: 200, body: { items: [item()], unscoped_count: 0 } }),
    async (calls) => {
      const code = await runCommand(["approvals", "list", "--all", "--project", "p1", "--kind", "gate", "--gateway", "http://gw", "--json"]);
      assert.equal(code, 0);
      assert.equal(calls[0]!.url, "http://gw/v1/approvals?status=all&project_id=p1&kind=gate");
    },
  );
  await withFetch(
    () => ({ status: 404, body: { detail: "nope" } }),
    async () => {
      assert.equal(await runCommand(["approvals", "show", "gate:x", "--gateway", "http://gw"]), EXIT_NOT_FOUND);
    },
  );
  await withFetch(
    (c) => (c.method === "GET" ? { status: 200, body: item() } : { status: 409, body: { detail: "expired" } }),
    async () => {
      assert.equal(await runCommand(["approvals", "approve", "gate:run_1", "--gateway", "http://gw"]), EXIT_CONFLICT);
    },
  );
});

test("runs approve and proposals approve are aliases on the new API", async () => {
  await withFetch(
    (c) => ({ status: 200, body: c.method === "GET" ? item() : item({ status: "approved" }) }),
    async (calls) => {
      await runCommand(["runs", "approve", "run_1", "--gateway", "http://gw"]);
      assert.equal(calls[1]!.url, "http://gw/v1/approvals/gate:run_1/decision");
      await runCommand(["proposals", "approve", "abc", "--gateway", "http://gw"]);
      assert.equal(calls[3]!.url, "http://gw/v1/approvals/change:abc/decision");
    },
  );
});
