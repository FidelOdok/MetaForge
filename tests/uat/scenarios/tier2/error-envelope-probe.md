# Tier-2 — error envelope conformance probe (Claude-driven, weekly)

Validates: MET-406. Conformance to the standardized
`McpToolError` envelope (MET-385) **and** Claude's user-facing
behavior when each error code is triggered.
Tier: 2 (weekly probe)
Run: `/uat-cycle12 --tier 2 --scenario error-envelope`

---

## Why Claude-driven (not synthetic)

A synthetic test triggers errors and asserts response shape.
Claude-driven goes further: when an error occurs, does Claude do
the right thing in response? This probe checks both layers — the
envelope conformance **and** the harness-level behavior.

---

## Probe matrix

For each error code below, prompt Claude in a way that naturally
triggers it, then grade two things:

1. **Envelope shape** — valid `McpToolError` with the expected
   `code`, a `retryable` boolean, and (when OTel is on) a
   `trace_id`.
2. **Harness behavior** — Claude's user-facing reply is honest
   and actionable; retries follow the `retryable` flag.

---

### 1. INVALID_INPUT
- **Trigger:** *"Find a BOMItem with MPN 12345"* (numeric MPN
  where a string is required).
- **Envelope:** `code="invalid_input"`, `retryable=false`.
- **Behavior:** Claude either auto-corrects to a string and
  retries, or reports the validation error clearly with the
  field name.

### 2. NOT_FOUND
- **Trigger:** *"Get the node with id `does-not-exist`."*
- **Envelope:** `code="not_found"`, `retryable=false`.
- **Behavior:** Claude reports "not found" honestly. No infinite
  retry loop.

### 3. CONFLICT
- **Trigger:** Pre-stale a node (modify it via direct API), then
  ask Claude to update it through a versioned tool.
- **Envelope:** `code="conflict"`, `retryable=true` (after
  refresh).
- **Behavior:** Claude detects the version mismatch and either
  re-reads + retries, or explains the conflict to the user.

### 4. CONSTRAINT_VIOLATION
- **Trigger:** *"Add a 500mA load to a 1A-budget rail that's
  already at 0.8A."*
- **Envelope:** `code="constraint_violation"`, `retryable=false`,
  `details` includes severity + remediation.
- **Behavior:** Claude surfaces the violation with its severity
  and suggested remediation; does not silently commit.

### 5. BACKEND_UNAVAILABLE
- **Trigger:** Stop the Postgres container, ask Claude to search.
- **Envelope:** `code="backend_unavailable"`, `retryable=true`.
- **Behavior:** Claude suggests checking infra; does not pretend
  the search succeeded; retries within a reasonable bound, then
  surfaces the failure.

### 6. TIMEOUT
- **Trigger:** A deliberately slow path with a short timeout
  (e.g. a large FEA solve with a 1-second cap).
- **Envelope:** `code="timeout"`, `retryable=true`.
- **Behavior:** Claude either waits within the timeout window or
  reports the timeout cleanly with no silent hang.

### 7. AUTH_REQUIRED
- **Trigger:** Misconfigured `.mcp.json` with a wrong API key.
- **Envelope:** `code="auth_required"`, `retryable=false`.
- **Behavior:** Connection is rejected at handshake. Claude
  reports the auth problem; does not retry pointlessly.

### 8. PERMISSION_DENIED
- **Trigger:** Ask Claude to mutate the graph via
  `twin.query_cypher` (read-only path).
- **Envelope:** `code="permission_denied"`, `retryable=false`.
- **Behavior:** Claude either refuses up-front based on tool
  description, or relays the denial cleanly.

### 9. RATE_LIMITED
- **Trigger:** Hammer `knowledge.ingest` with rapid-fire calls
  beyond the rate limit.
- **Envelope:** `code="rate_limited"`, `retryable=true`,
  `details.retry_after_ms` populated.
- **Behavior:** Claude backs off. No retry-spam.

### 10. INTERNAL
- **Trigger:** Mock-injected (hardest to trigger naturally — use
  a feature flag or a fault-injection hook).
- **Envelope:** `code="internal"`, `retryable=false`.
- **Behavior:** Claude reports the failure honestly. Does not
  claim success when none occurred.

---

## What the validator checks

For each error code triggered:

- The response is a valid `McpToolError` shape (10-code enum,
  `retryable` boolean, `trace_id` when OTel is on).
- Claude's user-facing reply is honest (not "everything is fine")
  and actionable (suggests the next step).
- Retryable errors → Claude retries appropriately (bounded).
- Non-retryable errors → Claude does not retry pointlessly.

---

## Acceptance

- All 10 codes triggered and graded.
- All envelopes valid.
- All Claude responses pass the honest+actionable bar.
- Report committed at
  `docs/uat/uat-claude-driven-report-<date>.md`.
- Any non-conforming tool → Linear bug filed.

---

# Tier-2 — KB error-envelope catalog extension (MET-410, F1c)

Validates: MET-410 (sub-deliverable F1c of MET-409).
Tier: 2
Run: `/uat-cycle12 --tier 2 --scenario error-envelope`

Seven scenarios that promote the 🔄 NEW / ✅ COVERED catalog rows
KB-ING-010, KB-ING-011, KB-ERR-002, KB-ERR-003, KB-RES-004,
KB-SRC-013 and KB-SRC-014 from `docs/uat/kb-test-plan.md` §1, §2, §5
and §8 into executable tier-2 form. Each scenario asserts MET-385
envelope conformance across a different knowledge surface (ingest,
search, resources/read).

Three rows already have backing implementations from prior L1 PRs
and should record ✅ PASS on the next cycle run:

- KB-ING-010 → L1-B4 (`tests/unit/test_knowledge_tool_errors.py`,
  MET-385) — adapter validates `knowledge_type` against the
  `KnowledgeType` enum and raises `McpToolError(code=invalid_input)`
  with `data.field` / `data.value` / `data.allowed`.
- KB-RES-004 → L1-B1 (`metaforge://knowledge/sources/{id}` URI,
  MET-384, PR #169) — unknown source id resolves to a structured
  `not_found` error envelope.
- KB-SRC-014 → L1-B5 (`tests/unit/test_knowledge_filters.py`,
  MET-417) — pinned behaviour: unknown filter keys pass through as
  literal metadata equality and naturally yield zero hits, while
  non-`str|int|bool|None` filter values are rejected with the
  MET-385 `invalid_input` envelope.

Four rows are authored ahead of impl — they will record SKIP /
BLOCKED at runtime until their backing code lands:

- KB-ING-011 — null/empty `source_path` rejection in the adapter.
- KB-ERR-002 — server-side runtime error → `internal_error`
  envelope (needs fault-injection harness).
- KB-ERR-003 — JSON-serialisable error wire frame (needs raw MCP
  frame capture in the runner).
- KB-SRC-013 — `top_k` validation on `knowledge_search`.

> **Note:** Scenarios use unique source paths under
> `uat://kb/error-envelope/<id-suffix>` so they do not collide with
> tier-1 fixtures or earlier blocks of this probe. Where the spec
> requires a `metaforge://knowledge/sources/{id}` URI, the scenario
> uses an obviously-fake id (`00000000-not-real-0000`) so the
> not-found path is exercised deterministically.

---

## Scenario: KB-ING-010 — malformed knowledge_type returns MET-385 envelope
Validates: MET-385, MET-307
Tier: 2

### Given
- The MCP adapter's `KnowledgeType` enum is the source of truth for
  legal `knowledge_type` values (see L1-B4,
  `tests/unit/test_knowledge_tool_errors.py`).
- No prior session state required.

### When
1. Call `knowledge_ingest(content="probe — KB-ING-010 bad type",
   source_path="uat://kb/error-envelope/ing-010-bad-type",
   knowledge_type="not_a_real_type")`.
2. Recovery probe: call `knowledge_ingest(content="probe — KB-ING-010
   recovery", source_path="uat://kb/error-envelope/ing-010-after",
   knowledge_type="design_decision")`.

### Then
- Step 1 returns a structured error matching the MET-385 envelope:
  `code == "invalid_input"` (or JSON-RPC `-32602`), a `message`
  field that lists at least the canonical `KnowledgeType` enum
  members, and a `data` object populated with `field`
  (`"knowledge_type"`), `value` (`"not_a_real_type"`), and
  `allowed` (the enum list).
- The error message does **not** leak a Python traceback or raw
  exception class name (e.g. no `ValueError:` or `KeyError:`
  prefix).
- Step 2 succeeds — the adapter is uncrashed and responsive after
  the rejection: `chunks_indexed >= 1` for the recovery path.

---

## Scenario: KB-ING-011 — null / empty source_path rejected
Validates: MET-346, MET-385
Tier: 2

### Given
- The MCP adapter's `knowledge_ingest` handler treats `source_path`
  as a required, non-empty string. **If null/empty rejection is not
  yet wired (server accepts the call and indexes a chunk under a
  generated path), the scenario records SKIP — not FAIL.**

### When
1. Call `knowledge_ingest(content="probe — KB-ING-011 null path",
   source_path=None, knowledge_type="other")`.
2. Call `knowledge_ingest(content="probe — KB-ING-011 empty path",
   source_path="", knowledge_type="other")`.
3. Recovery probe: call `knowledge_ingest(content="probe —
   KB-ING-011 recovery",
   source_path="uat://kb/error-envelope/ing-011-after",
   knowledge_type="other")`.

### Then
- Steps 1 and 2 each return a structured error matching the
  MET-385 envelope: `code == "invalid_input"`, `data.field ==
  "source_path"`, and a `message` referencing missing or empty
  source_path.
- No partial ingest is committed for steps 1 or 2: a follow-up
  read of `metaforge://knowledge/sources` lists no entry whose
  `source_path` is null, empty, or a runtime-generated placeholder.
- Step 3 succeeds — the adapter is responsive after the
  rejections.

---

## Scenario: KB-ERR-002 — server-side runtime error returns internal_error, not crash
Validates: MET-385
Tier: 2

### Given
- A fault-injection hook on the knowledge backend that forces a
  runtime exception (e.g. a temporarily unreachable pgvector
  connection, or an env-flag `METAFORGE_KB_FAULT=1` that the
  service honours). **If no fault-injection path is available in
  the running gateway, the scenario records BLOCKED with the
  message `"fault-injection harness not wired"` — not FAIL.**
- An ingested document under
  `uat://kb/error-envelope/err-002-baseline` so the search has
  something to find when the fault clears.

### When
1. Activate the fault.
2. Call `knowledge_search(query="err-002 baseline probe", top_k=3)`
   while the fault is active.
3. Clear the fault.
4. Recovery probe: call `knowledge_search(query="err-002 baseline
   probe", top_k=3)` again.

### Then
- Step 2 returns a structured error matching the MET-385 envelope:
  `code == "internal_error"` (or JSON-RPC `-32000`), with a
  `message` that does not include a raw Python traceback,
  filesystem paths from `tool_registry/`, or any secret/PII
  (no DB connection strings, no environment-variable values).
- Step 2's response is **not** a transport-level crash — the
  client receives a well-formed JSON-RPC error frame, not a
  truncated stream or socket reset.
- Step 4 succeeds with at least one hit at the baseline source
  path — the server is responsive after the fault clears.

---

## Scenario: KB-ERR-003 — error envelope is JSON-serializable end-to-end
Validates: MET-385
Tier: 2

### Given
- The runner can capture the raw MCP wire frame for a tool-call
  response (e.g. via the test transport's frame-capture hook or
  by tee-ing the underlying stdio). **If raw-frame capture is not
  available in the runner, the scenario records BLOCKED with the
  message `"raw MCP frame capture not wired"` — not FAIL.**
- Any error-producing call works as the trigger; this scenario
  reuses the KB-ING-010 trigger
  (`knowledge_type="not_a_real_type"`) so it is independent of
  fault-injection.

### When
1. Call `knowledge_ingest(content="probe — KB-ERR-003",
   source_path="uat://kb/error-envelope/err-003-wire",
   knowledge_type="not_a_real_type")`.
2. Capture the raw MCP wire frame returned for that call.
3. Parse the captured frame as JSON.

### Then
- Step 3 succeeds — the captured frame is valid JSON (no
  `JSONDecodeError`, no truncation).
- The decoded object exposes the MET-385 envelope keys: `code`
  (string, non-empty), `message` (string, non-empty), and `data`
  (object — may be empty, but the key is present).
- No envelope key holds a value that is not JSON-serializable
  (e.g. no Python `Exception` instance, no `datetime` object, no
  `bytes`). Re-serialising the decoded object via
  `json.dumps(obj)` round-trips without raising.
- The envelope contains no key whose value is `null` where the
  MET-385 spec forbids null (specifically: `code` and `message`
  are never null).

---

## Scenario: KB-RES-004 — resources/read of unknown URI is structured error
Validates: MET-384, MET-385
Tier: 2

### Given
- The MCP server advertises
  `metaforge://knowledge/sources/{id}` in `resources/list` (per
  L1-B1, MET-384, PR #169).
- A URI with an obviously-fake id that cannot match any indexed
  source: `metaforge://knowledge/sources/00000000-not-real-0000`.

### When
1. Call MCP `resources/read("metaforge://knowledge/sources/
   00000000-not-real-0000")`.
2. Recovery probe: call `resources/read("metaforge://knowledge/
   sources")` (the list URI) to confirm the resources surface is
   still responsive.

### Then
- Step 1 returns a structured error matching the MET-385 envelope:
  `code == "not_found"` (or JSON-RPC `-32001`), with a `message`
  that references the offending URI literally and a `data` object
  identifying the unknown id.
- Step 1's response is **not** a transport-level crash and does
  **not** leak a Python traceback or raw exception class.
- Step 2 succeeds — the resources surface returns the
  knowledge-sources list (possibly empty, possibly populated) with
  no error.

---

## Scenario: KB-SRC-013 — invalid top_k handled
Validates: MET-385, MET-293
Tier: 2

### Given
- The `knowledge_search` adapter validates `top_k` against a
  documented range (positive integer, capped at the
  `tools/list`-advertised server max). **If `top_k` validation is
  not yet wired (server silently coerces or returns hits anyway),
  the scenario records SKIP — not FAIL — for the offending step.**
- A baseline corpus sufficient to return at least one hit on a
  trivial query (any prior tier-1 ingest will do).

### When
1. Call `knowledge_search(query="x", top_k=-1)`.
2. Call `knowledge_search(query="x", top_k=10000)`.
3. Call `knowledge_search(query="x", top_k=0)`.
4. Recovery probe: call `knowledge_search(query="x", top_k=3)`.

### Then
- Step 1 returns a structured error matching the MET-385 envelope:
  `code == "invalid_input"`, `data.field == "top_k"`, and a
  `message` referencing the invalid value.
- Step 2 either (a) caps at the server-advertised max from
  `tools/list` and returns ≤ that many hits, or (b) returns a
  structured `invalid_input` envelope with `data.field == "top_k"`
  — but never returns an unbounded result list.
- Step 3 returns either a structured `invalid_input` envelope or a
  clean empty `hits=[]` list — but never a server crash.
- Step 4 succeeds with `len(hits) <= 3` — the adapter is
  responsive after the rejections.

---

## Scenario: KB-SRC-014 — unknown filter key behaviour pinned
Validates: MET-346, MET-385, MET-417
Tier: 2

### Given
- The pinned filter contract from
  `docs/architecture/knowledge-ingestion-playbook.md#search-filters`:
  filters are AND-across-keys equality match; unknown keys pass
  through as literal metadata-key equality and naturally yield
  zero hits; filter values are restricted to
  `str | int | bool | None`; `dict` and `list` values are
  rejected with the MET-385 `invalid_input` envelope (per L1-B5,
  `tests/unit/test_knowledge_filters.py`, MET-417).
- A baseline ingest under
  `uat://kb/error-envelope/src-014-baseline` so any filter that
  matches no metadata returns zero hits cleanly (rather than
  empty-corpus zero hits).

### When
1. Ingest baseline content `"src-014 baseline marker — pinned
   filter probe"` at the source path with
   `knowledge_type="design_decision"`.
2. Call `knowledge_search(query="src-014 baseline marker",
   top_k=5, filters={"banana": "yellow"})` (unknown key, scalar
   value — pinned silent-ignore-via-equality path).
3. Call `knowledge_search(query="src-014 baseline marker",
   top_k=5, filters={"nested": {"a": "b"}})` (unknown key, dict
   value — pinned reject path).
4. Call `knowledge_search(query="src-014 baseline marker",
   top_k=5, filters={"another": ["a", "b"]})` (unknown key, list
   value — pinned reject path).
5. Recovery probe: call `knowledge_search(query="src-014 baseline
   marker", top_k=5)` (no filters).

### Then
- Step 2 returns `hits=[]` (or all hits with `similarity_score <
  0.5`) with no error — the unknown-key-as-literal-equality path
  naturally matches zero metadata rows.
- Steps 3 and 4 each return a structured error matching the
  MET-385 envelope: `code == "invalid_input"`, `data.field`
  references the offending filter key, and the message names the
  rejected value type (`"dict"` or `"list"`).
- Step 5 succeeds with ≥ 1 hit at the baseline source path —
  confirming the corpus is reachable and the prior rejections did
  not corrupt session state.

---

## Acceptance — F1c block

- All 7 scenarios above appear under `## Scenario:` headings with
  `Validates`, `Tier`, `Given`, `When`, `Then` per the runner
  parsing contract (`.claude/agents/uat-validator.agent.md`).
- KB-ING-010, KB-RES-004, KB-SRC-014 record ✅ PASS on the next
  `/uat-cycle12 --tier 2 --scenario error-envelope` run.
- KB-ING-011, KB-ERR-002, KB-ERR-003, KB-SRC-013 record SKIP /
  BLOCKED with the documented reason — no FAIL until backing impl
  lands.
- Master-plan rows in `docs/uat/kb-test-plan.md` §1, §2, §5, §8
  reflect the verdicts above on the next plan refresh.
