# Tier-2 — Provenance / source-attribution probe

Validates that the MET-320 / MET-322 attribution chain is intact
when an ingested document round-trips through the assembler. Run
weekly.

---

## Scenario: every search hit has an attributable source_path
Validates: MET-320 (story: agent proposals are fully traceable)
Tier: 2

### Given
- MetaForge MCP server reachable.

### When
1. Ingest content "provenance-probe distinctive token jw-3x9 —
   used to verify attribution end-to-end." at
   `uat://tier2/provenance/probe.md` with knowledge_type
   `"design_decision"`.
2. Search for `"jw-3x9 attribution"` with top_k=3.

### Then
- The returned hit list is non-empty.
- Every hit has a non-empty `source_path` field.
- Every hit has either `chunk_index` or `total_chunks` populated
  (cite-ability requirement — agents should be able to point at
  the exact slice).
- The hit referencing our probe has `source_path` exactly equal to
  `"uat://tier2/provenance/probe.md"`.

---

## Scenario: knowledge ingestion + search produces a citable trail in logs
Validates: MET-320 + observability wiring
Tier: 2

### Given
- Loki datasource reachable via Grafana MCP.

### When
1. Ingest content "logged-probe-token cz-7p — verifies log trail."
   at `uat://tier2/provenance/log-probe.md`.
2. Search for `"cz-7p log trail"`.
3. Query Loki for the last 5 minutes:
   `{service_name="metaforge-gateway"} |~ "knowledge_ingest"` AND
   `{service_name="metaforge-gateway"} |~ "knowledge_search"`.

### Then
- Step 1 + 2 succeed (no error responses).
- Step 3's Loki results include at least one log line for each
  event type (ingest + search).
- At least one log line carries our probe's source_path
  (`uat://tier2/provenance/log-probe.md`) — full lineage from
  user intent → tool call → log is preserved.
