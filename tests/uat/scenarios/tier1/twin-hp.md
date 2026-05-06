# Tier-1 — `twin.*` happy path (HP-TWIN)

Validates: MET-412 (sub-deliverable 3/10 of MET-409).
Tier: 1
Run: `/uat-cycle12 --tier 1 --scenario twin-hp`

Ten happy-path scenarios for the Twin MCP tool group (MET-382)
from a Claude-as-real-user perspective. Every scenario assumes
the MetaForge MCP server is connected and the five Twin tools —
`get_node`, `find_by_property`, `thread_for`,
`constraint_violations`, `query_cypher` — are listed in
`tool/list`.

> **Note:** Several scenarios depend on a seeded twin graph.
> If the test fixture is missing (`seed_twin_fixtures.py` not
> run), scenarios that lookup specific UUIDs report BLOCKED.

---

## Scenario: HP-TWIN-01 — get_node by id returns properties + first hop
Validates: MET-382
Tier: 1

### Given
- A known node UUID from the seeded twin fixture (e.g. a
  Requirement, BOMItem, or DesignElement).

### When
1. Call `twin.get_node` with the UUID.

### Then
- Response includes a `properties` dict (id, type, createdAt,
  domain-specific keys) and a `neighbors` list of first-hop
  edges. Both fields are present and shape-valid.

---

## Scenario: HP-TWIN-02 — find_by_property by MPN returns BOMItem
Validates: MET-382
Tier: 1

### Given
- A seeded BOMItem with `mpn="STM32H723VGT6"`.

### When
1. Call `twin.find_by_property` with `label="BOMItem"` and
   `properties={"mpn": "STM32H723VGT6"}`.

### Then
- Exactly one hit returned.
- The hit has `label="BOMItem"` and full property bag matching
  the seed.

---

## Scenario: HP-TWIN-03 — thread_for depth=3 walks digital thread
Validates: MET-382 (thread traversal)
Tier: 1

### Given
- A seeded Requirement node with downstream DesignElement and
  TestExecution nodes wired through the digital-thread edge
  schema.

### When
1. Call `twin.thread_for` from the Requirement, `depth=3`.

### Then
- The returned subgraph contains at least one Requirement,
  one DesignElement, and one TestExecution (or Test node).
- All edges in the response are within 3 hops of the start node.

---

## Scenario: HP-TWIN-04 — constraint_violations empty when clean
Validates: MET-382, MET-383
Tier: 1

### Given
- A clean project (no violations seeded).

### When
1. Call `twin.constraint_violations` for the project.

### Then
- Response is an empty array, not an error or `null`.

---

## Scenario: HP-TWIN-05 — constraint_violations severity ordering
Validates: MET-382, MET-383
Tier: 1

### Given
- A project seeded with one `error`, one `warning`, and one
  `info` violation.

### When
1. Call `twin.constraint_violations`.

### Then
- The response array is ordered by severity: all `error` items
  first, then `warning`, then `info`. Within a severity bucket
  the order is stable but unspecified.

---

## Scenario: HP-TWIN-06 — query_cypher (read-only) succeeds
Validates: MET-382 (Cypher passthrough)
Tier: 1

### Given
- A seeded graph with multiple DesignElement types.

### When
1. Call `twin.query_cypher` with
   `MATCH (d:DesignElement) RETURN d.type AS type, count(*) AS n`.

### Then
- Response is an aggregated rowset with `type` and `n` columns.
- No mutation occurred (re-running returns identical counts).

---

## Scenario: HP-TWIN-07 — cross-domain edge traversal
Validates: MET-382 (typed edges)
Tier: 1

### Given
- A seeded fixture where a component (e.g. `U17`) is linked to
  two others by `THERMALLY_COUPLED_TO` edges.

### When
1. Use `twin.get_node` (or `thread_for` with `edge_types=
   ["THERMALLY_COUPLED_TO"]`) to enumerate U17's coupled neighbors.

### Then
- Both expected coupled components appear in the neighbor list.
- No unrelated edge types are returned.

---

## Scenario: HP-TWIN-08 — versioning chain (SUPERSEDES) walked
Validates: MET-382, twin-core versioning
Tier: 1

### Given
- A DecisionRecord with at least one prior version connected by a
  `SUPERSEDES` edge.

### When
1. Call `twin.thread_for` from the latest DecisionRecord with
   `edge_types=["SUPERSEDES"]`, `direction="incoming"`,
   `depth=10`.

### Then
- The full version chain is returned in supersedes order
  (latest at root, earliest at leaf).

---

## Scenario: HP-TWIN-09 — provenance edge (PRODUCED_BY) followed
Validates: MET-382 + MET-387 (session provenance)
Tier: 1

### Given
- A node produced by a known skill execution (e.g. a
  reconciliation proposal).

### When
1. Call `twin.thread_for` from the node with
   `edge_types=["PRODUCED_BY"]`, `depth=1`.

### Then
- A `Session` (or `SkillExecution`) node is returned with
  populated `skill_name` and timestamp fields.

---

## Scenario: HP-TWIN-10 — project-scoped query respects ctx.project_id
Validates: MET-387 (per-call context), MET-401
Tier: 1

### Given
- Two seeded projects (A and B) each containing distinct
  BOMItems.

### When
1. Open an MCP session with `ctx.project_id = A`.
2. Call `twin.find_by_property` with `label="BOMItem"`,
   no other filters.

### Then
- Only Project A's BOMItems are returned.
- None of Project B's BOMItems leak across.

---

## Acceptance

- All 10 scenarios PASS.
- Report committed.
