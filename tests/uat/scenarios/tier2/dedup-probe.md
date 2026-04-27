# Tier-2 — Knowledge dedup observability probe

Validates MET-307's update-event semantics: when content is
re-ingested at the same source_path, the consumer drops the prior
chunks before re-indexing — no orphan duplicates. Driven via
direct MCP calls + a Loki search to confirm the
`knowledge_consumer_predelete` event fires. Run weekly.

---

## Scenario: re-ingest at same source_path drops stale chunks
Validates: MET-307
Tier: 2

### Given
- MetaForge MCP server reachable.
- Loki datasource reachable via Grafana MCP.

### When
1. Ingest content "Dedup-probe v1: aluminium 6061 prototype."
   at `uat://tier2/dedup/probe.md` with knowledge_type
   `"design_decision"`. Capture the timestamp before this call —
   we'll filter Loki by it.
2. Ingest content "Dedup-probe v2: titanium grade 5 replacement."
   at the **same** `uat://tier2/dedup/probe.md`.
3. Search for `"Dedup-probe"` with top_k=10.
4. Query Loki for the last 5 minutes:
   `{service_name="metaforge-gateway"} |~ "knowledge_consumer_predelete"`.

### Then
- Step 3 returns hits referencing **only the v2 content** — the
  literal phrase "aluminium 6061 prototype" is absent from any
  returned chunk.
- Step 3's matching hit's source_path is
  `"uat://tier2/dedup/probe.md"`.
- Step 4 returns at least one Loki line whose JSON includes
  `event="knowledge_consumer_predelete"` and a non-zero
  `deleted` count for the source_path
  `work_product://...` (or `uat://tier2/dedup/probe.md`,
  depending on the consumer's source_path mapping).

---

## Scenario: distinct source_paths do NOT trigger dedup
Validates: MET-307 negative case (no false positives)
Tier: 2

### Given
- MetaForge MCP server reachable.

### When
1. Ingest content "Distinct-probe alpha." at
   `uat://tier2/dedup/distinct-a.md`.
2. Ingest content "Distinct-probe beta." at
   `uat://tier2/dedup/distinct-b.md`.
3. Search for `"Distinct-probe"` with top_k=5.

### Then
- Both source_paths appear in the hit list — different
  source_paths must coexist; dedup is keyed on source_path, not
  on content similarity.
