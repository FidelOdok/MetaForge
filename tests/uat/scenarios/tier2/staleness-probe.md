# Tier-2 — Staleness observability probe

Validates that the MET-323 staleness machinery actually emits its
metric when fragments are filtered. Driven via Grafana (Prometheus
+ Loki) rather than direct MCP calls. Run weekly.

---

## Scenario: context_truncated metric increments under tight budget
Validates: MET-323, MET-326 (metaforge_context_truncated_total wiring)
Tier: 2

### Given
- The Prometheus datasource is reachable from Grafana MCP
  (preflight via `mcp__grafana__list_loki_label_values`).
- The MetaForge gateway has been booted at least once with
  observability enabled (Postgres + Neo4j up — same docker-compose
  stack).

### When
1. Cause a tight-budget assemble through the gateway. The simplest
   way is to ingest 3 longish documents (≥ 400 tokens each) at
   distinct source_paths via `knowledge.ingest`, then call the
   gateway's `/context/assemble` endpoint with a 200-token budget.
   (If `/context/assemble` is not exposed yet, skip step 1 and
   record this scenario as BLOCKED.)
2. Query Prometheus for
   `sum(metaforge_context_truncated_total)` over the last 5
   minutes via `mcp__grafana__query_prometheus`.
3. Query Loki for the structured event
   `{service_name="metaforge-gateway"} |~ "context_truncated"`
   over the last 5 minutes via `mcp__grafana__query_loki_logs`.

### Then
- Step 2 returns at least one positive value (the counter
  incremented when the budget pruned fragments).
- Step 3 returns at least one log line whose JSON includes
  `event="context_truncated"` and a `dropped_count >= 1`.

---

## Scenario: superseded fragments do not appear in subsequent retrievals
Validates: MET-323
Tier: 2

### Given
- The `KnowledgeService` exposes the standard ingest+search MCP
  tools.

### When
1. Ingest content "stale-marker initial — fact A holds." at
   `uat://tier2/staleness/initial`.
2. Ingest content "stale-marker replacement — fact A is now superseded."
   at the same source_path `uat://tier2/staleness/initial`
   (re-ingest under same path triggers the consumer's pre-delete).
3. Search for `"stale-marker"` with top_k=10.

### Then
- The returned hit list contains the *replacement* content
  ("now superseded").
- The returned hit list does **not** contain the literal phrase
  "fact A holds." from the original — pre-delete stripped the
  stale chunk.
