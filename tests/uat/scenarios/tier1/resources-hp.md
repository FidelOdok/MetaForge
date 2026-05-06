# Tier-1 — MCP resources happy path (HP-RES)

Validates: MET-416 (sub-deliverable 7/10 of MET-409).
Tier: 1
Run: `/uat-cycle12 --tier 1 --scenario resources-hp`

Ten happy-path scenarios for the MCP resources surface (MET-384)
from a Claude-as-real-user perspective. Resources are read-only,
addressable URIs — distinct from tools (which are callable).

---

## Scenario: HP-RES-01 — resources/list returns ≥ 5 templates
Validates: MET-384 (resources/list protocol)
Tier: 1

### Given
- The MCP server is connected; resources surface is enabled.

### When
1. Call `resources/list` at session start.

### Then
- At least 5 resource URI templates are returned. Expected
  templates include `metaforge://knowledge/sources`,
  `metaforge://knowledge/sources/{id}`,
  `metaforge://twin/projects`,
  `metaforge://twin/projects/{id}/nodes`,
  `metaforge://twin/models`. (Order doesn't matter; presence does.)

---

## Scenario: HP-RES-02 — knowledge/sources returns paginated list
Validates: MET-384 + MET-307
Tier: 1

### Given
- At least 3 ingested KnowledgeSource rows.

### When
1. Call `resources/read` for `metaforge://knowledge/sources`.

### Then
- The response is a list of source rows, each with `name`,
  `status`, `indexedAt`, `fragmentCount`.
- Pagination cursor is present when the total exceeds page size.

---

## Scenario: HP-RES-03 — individual source URI returns chunks
Validates: MET-384 (per-source detail)
Tier: 1

### Given
- A known KnowledgeSource id.

### When
1. Call `resources/read` for
   `metaforge://knowledge/sources/{id}`.

### Then
- The response includes a chunk list. Each chunk has `content`,
  `heading`, `chunk_index`.

---

## Scenario: HP-RES-04 — twin/projects returns project list
Validates: MET-384 + MET-382
Tier: 1

### Given
- At least one Project node exists.

### When
1. Call `resources/read` for `metaforge://twin/projects`.

### Then
- The response is a list of Project rows, each with `id`,
  `name`, `createdAt`.

---

## Scenario: HP-RES-05 — project-scoped nodes URI
Validates: MET-384 + MET-401
Tier: 1

### Given
- A project named "Aurora" with seeded twin nodes.

### When
1. Call `resources/read` for
   `metaforge://twin/projects/{aurora-id}/nodes`.

### Then
- The response is a paginated node list scoped to Aurora.
- No nodes from other projects appear.

---

## Scenario: HP-RES-06 — twin/models returns TwinModel list
Validates: MET-384
Tier: 1

### Given
- At least one TwinModel row.

### When
1. Call `resources/read` for `metaforge://twin/models`.

### Then
- The response lists TwinModel nodes (id, name, version,
  createdAt at minimum).

---

## Scenario: HP-RES-07 — pagination cursor advances correctly
Validates: MET-384 (pagination)
Tier: 1

### Given
- A resource with > 1 page worth of rows.

### When
1. Read the first page, capture the `nextCursor`.
2. Read again with that cursor.

### Then
- The second page returns the next batch.
- No row appears in both pages.
- The final page returns no `nextCursor` (or `null`).

---

## Scenario: HP-RES-08 — resources/list_changed fires on new ingest
Validates: MET-384 (subscription / list_changed)
Tier: 1

### Given
- An MCP session subscribed to `resources/list_changed`.

### When
1. Call `knowledge.ingest` with a new fixture.

### Then
- A `resources/list_changed` (or equivalent) notification fires
  within 1 second.
- A subsequent `resources/list` shows the new source.

---

## Scenario: HP-RES-09 — read-only enforcement on write attempt
Validates: MET-384 (read-only contract)
Tier: 1

### Given
- The resources surface (resources are read-only by spec).

### When
1. Attempt a write through the resources URI (e.g. PUT/POST or
   ask Claude to "create a project via the resources API").

### Then
- The attempt is rejected. Either Claude refuses based on the
  surface contract, or the server returns a structured error
  (`PERMISSION_DENIED` per MET-385 envelope) — never silent
  success, never crash.

---

## Scenario: HP-RES-10 — auto-context attachment in Claude Code
Validates: MET-384 + harness integration
Tier: 1

### Given
- A `.mcp.json` configured to auto-attach
  `metaforge://twin/projects/{X}/nodes` at session start.

### When
1. Open a Claude Code session with that config.
2. Ask a question whose answer lives in the attached resource
   (e.g. "How many DesignElements are in this project?")
   without explicitly calling any tool.

### Then
- Claude answers from the attached resource (no extra tool
  calls needed for that question).
- The trace shows the resource was loaded at session start.

---

## Acceptance

- All 10 scenarios PASS.
- Report committed.
