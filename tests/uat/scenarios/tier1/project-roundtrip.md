# Tier-1 — `project.*` happy path (HP-PROJ)

Validates: MET-432 — UAT for the Project MCP adapter (MET-427).
Tier: 1
Run: `/uat-cycle12 --tier 1 --scenario project-roundtrip`

Ten round-trip scenarios for the Project MCP tool group (MET-427)
from a Claude-as-real-user perspective. Every scenario assumes the
MetaForge MCP server is connected and the three project tools —
`project.create`, `project.list`, `project.get` — are listed in
`tools/list`.

> **Note:** Three scenarios depend on follow-up tickets and are
> tagged BLOCKED with a pointer:
>
> - HP-PROJ-05 → MET-387 (per-call MCP project context)
> - HP-PROJ-07 → follow-up "project.create writes a Twin node"
> - HP-PROJ-10 → follow-up "project.delete MCP tool"
>
> All ten remain valuable as a forcing function for the follow-ups.

---

## Scenario: HP-PROJ-01 — `project.create` returns a valid UUID
Validates: MET-427
Tier: 1

### Given
- The unified MCP server is running with a `project_backend` wired
  (in-memory or Postgres — backend choice does not matter).
- No project named `demo-flight-controller` exists yet.

### When
1. Call `project.create` with arguments
   `{name: "demo-flight-controller", description: "T1 round-trip test"}`.

### Then
- Response has an `id` that parses as a UUID (any version).
- `name` equals `"demo-flight-controller"`.
- `description` equals `"T1 round-trip test"`.
- `status` defaults to `"draft"`.
- `created_at` parses as ISO-8601 and is within the last 60 seconds.
- `agent_count` is `0`.
- `work_products` is an empty list.

---

## Scenario: HP-PROJ-02 — `project.list` includes the new project
Validates: MET-427
Tier: 1

### Given
- HP-PROJ-01 just succeeded.

### When
1. Call `project.list` with no arguments.

### Then
- `total` is `≥ 1`.
- The project from HP-PROJ-01 appears in `projects` with the same
  `id` and `name`.

---

## Scenario: HP-PROJ-03 — `project.get` by id returns the full record
Validates: MET-427
Tier: 1

### Given
- The `id` from HP-PROJ-01.

### When
1. Call `project.get` with `{id: <id from HP-PROJ-01>}`.

### Then
- Response is non-null.
- `id`, `name`, `description`, `status`, `created_at`, and
  `last_updated` all match the values returned by HP-PROJ-01.

---

## Scenario: HP-PROJ-04 — `project.get` by name returns the same record
Validates: MET-427
Tier: 1

### Given
- The project from HP-PROJ-01 is still present.

### When
1. Call `project.get` with `{name: "demo-flight-controller"}`.

### Then
- Response is non-null.
- `id` matches the `id` from HP-PROJ-01.
- Full body is byte-for-byte equal to the body returned by
  HP-PROJ-03 (modulo `last_updated` if the backend touched it
  between the two reads).

---

## Scenario: HP-PROJ-05 — BLOCKED — `project.list` respects ctx.project_id
Validates: MET-387 (per-call MCP context) + MET-427
Tier: 1
Status: BLOCKED — MET-427 ships `project.list` unscoped. Tenant
scoping arrives with the context-aware refactor (sibling follow-up).

### Given
- Two projects exist: A (created under `ctx.project_id = A`) and
  B (created under `ctx.project_id = B`).

### When
1. Call `project.list` while the active call context has
   `project_id = B`.

### Then
- Response includes project B and does **not** include project A.

---

## Scenario: HP-PROJ-06 — duplicate-name create is deterministic
Validates: MET-427
Tier: 1

### Given
- A project named `demo-flight-controller` already exists.

### When
1. Call `project.create` again with the same `name`.

### Then
- Outcome is deterministic across runs. Today (Phase 1, in-memory
  backend) the adapter accepts the duplicate name and returns a
  fresh UUID — name uniqueness is intentionally **not** enforced.
  The scenario PASSES when the second call succeeds with a new
  `id` distinct from the first.
- Follow-up: when the Postgres backend lands with a unique index
  on `name`, switch this scenario to expect a `project_name_taken`
  tool-level error.

---

## Scenario: HP-PROJ-07 — BLOCKED — `project.create` propagates to Twin
Validates: MET-427 follow-up — "project as Twin work product"
Tier: 1
Status: BLOCKED — MET-427 ships project storage decoupled from the
Twin graph. The follow-up wires a `Project` WorkProduct node so
agents can attach work products to a project via twin edges.

### Given
- A project from HP-PROJ-01.

### When
1. Call `twin.get_node` with the project `id`.

### Then
- Response is a `Project` work product whose `id` matches.

---

## Scenario: HP-PROJ-08 — `project.create` emits an audit event
Validates: MET-427 observability
Tier: 1

### Given
- The MCP server is running with structured logging enabled
  (the dev compose stack ships Loki by default).

### When
1. Call `project.create` with a unique name.
2. Query Loki for the log line emitted by the adapter (event
   name: `project_mcp_create`).

### Then
- Exactly one log entry matches the trace_id of the MCP call.
- The entry includes `project_id`, `project_name`, and
  (if set) `actor_id`.

---

## Scenario: HP-PROJ-09 — concurrent `project.create` calls are race-safe
Validates: MET-427
Tier: 1

### Given
- Clean backend (no prior projects with names matching the
  generated batch).

### When
1. Fire 10 concurrent `project.create` calls with distinct names
   (`stress-001` … `stress-010`).

### Then
- All 10 calls succeed.
- All 10 returned `id` values are distinct.
- A subsequent `project.list` shows all 10.

---

## Scenario: HP-PROJ-10 — BLOCKED — `project.delete` removes from list
Validates: MET-427 follow-up — "project.delete MCP tool"
Tier: 1
Status: BLOCKED — `ProjectBackend` already implements
`delete_project`, but MET-427 did not surface it as an MCP tool.
The follow-up exposes `project.delete`.

### Given
- A project from HP-PROJ-01.

### When
1. Call `project.delete` with `{id: <id from HP-PROJ-01>}`.
2. Call `project.list`.

### Then
- After delete, `project.list` does not include the id.

---

## Pass / Fail summary

| Scenario      | Status                                |
| ------------- | ------------------------------------- |
| HP-PROJ-01    | Expected PASS                         |
| HP-PROJ-02    | Expected PASS                         |
| HP-PROJ-03    | Expected PASS                         |
| HP-PROJ-04    | Expected PASS                         |
| HP-PROJ-05    | BLOCKED — depends on MET-387          |
| HP-PROJ-06    | Expected PASS (deterministic choice)  |
| HP-PROJ-07    | BLOCKED — follow-up: Twin propagation |
| HP-PROJ-08    | Expected PASS                         |
| HP-PROJ-09    | Expected PASS                         |
| HP-PROJ-10    | BLOCKED — follow-up: project.delete   |

Evidence file: `tests/uat/runs/<date>/project-roundtrip.md`.
