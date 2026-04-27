# UAT Tier-2 Observability — Claude-driven first run (2026-04-27)

**Scenario set**: `tests/uat/scenarios/tier2/{staleness,provenance,dedup}-probe.md`
**Validates**: MET-307, MET-320, MET-322, MET-323, MET-326
**Tier**: 2 (weekly cadence)
**Path**: validator surrogate (parent Claude Code session pre-dates `.mcp.json`; canonical path is `/uat-cycle12 --tier 2`)
**Surrogate driver**: `scripts/run_tier2_observability_validator_surrogate.py`
**Elapsed**: 19.21s wall (Loki-driven; no embedder cold-load on the surrogate side)
**Overall verdict**: **FAIL** — 5 PASS, 8 FAIL, 1 BLOCKED

---

## Summary

| Probe | Scenario | Verdict | Notes |
|-------|----------|---------|-------|
| staleness | 1: context_truncated metric increments | **BLOCKED** | gateway has no `/context/assemble` (per scenario's own contract) |
| staleness | 2: superseded fragments not in retrievals | 1 PASS / 1 FAIL | search returns no hits → fact-A-absent technically passes vacuously; v2 not surfaced |
| provenance | 1: every search hit has source_path | 1 PASS / 3 FAIL | gateway response missing `sourcePath`/`chunkIndex`/`totalChunks` → MET-376 |
| provenance | 2: ingestion + search produce log trail | 2 PASS / 1 FAIL | `knowledge_ingested` + `knowledge_search` events flow; source_path absent from log lines |
| dedup | 1: re-ingest drops stale chunks | 1 PASS / 2 FAIL | predelete event missing → MET-378; titanium content not surfaced (zero-vector search) |
| dedup | 2: distinct source_paths coexist | 1 FAIL | search returns prewarm noise instead of probe content (zero-vector search) |

---

## Gaps filed forward

Per the auto-file-FAIL policy, three follow-ups created in Cycle 2 (parent MET-372):

| Ticket | Title | Priority | Why |
|--------|-------|----------|-----|
| MET-376 | gateway `/api/v1/knowledge/search` response missing source_path field | High | Breaks attribution UX entirely; LLM consumers can't cite hit origin |
| MET-377 | gateway container missing `sentence-transformers` — search returns zero-vector results | High | Embeddings fall back to zero vectors; vector search is essentially random |
| MET-378 | KnowledgeConsumer not wired into gateway boot — predelete events never fire | Medium | Dedup works at storage layer, but consumer telemetry is invisible |

The 1 BLOCKED verdict (staleness/1) is **expected** — the scenario explicitly says "If `/context/assemble` is not exposed yet, skip step 1 and record this scenario as BLOCKED." The endpoint isn't built yet; that's a separate roadmap item, not a Cycle 2 regression.

---

## What worked

- **OTel pipeline is alive**: gateway → otel-collector → Loki captures `knowledge_ingested` + `knowledge_search` events with full `trace_id` / `span_id` lineage, sub-second after the HTTP call returns.
- **Provenance/2 ingest+search log lineage**: 6 ingest events + 4 search events in 5-minute window proves the observability pipeline is feeding Loki at expected cadence.
- **Re-ingest deduplication at the storage layer**: validated separately by Tier-1 Scenario 7 — works correctly when called via `KnowledgeService` directly. The Tier-2 FAIL on dedup/1 is about the *observability of dedup*, not whether dedup happens.

---

## What broke

### Pattern 1: search response is half-empty

`GET /api/v1/knowledge/search` returns hit objects with these fields:

```
['id', 'content', 'knowledgeType', 'metadata', 'sourceWorkProductId', 'createdAt']
```

Notably absent: **`source_path` / `sourcePath`**, **`chunk_index`**, **`total_chunks`**.

These fields exist on the underlying `LightRAGKnowledgeService.SearchHit` (Tier-1 Scenario 5 verified `chunk_index=0`, `total_chunks=1`, `source_path=...` are all populated when called directly). The gateway response model strips them on the way out. Filed as **MET-376**.

### Pattern 2: search returns wrong hits

Loki captured this warning on every gateway-side ingest:

```
event="sentence_transformers_not_installed" fallback="zero_vector"
```

The `metaforge-gateway` Docker image lacks `sentence-transformers`. Embeddings fall back to zero vectors. Vector similarity search becomes random tie-breaking → searching for `"Dedup-probe"` returns 5 unrelated documents from earlier in the day, including warmup noise. Filed as **MET-377**.

### Pattern 3: consumer telemetry invisible

`api_gateway/server.py` mentions the `KnowledgeConsumer` only in comments — never starts it. The events `knowledge_consumer_predelete`, `knowledge_consumer_indexed`, etc. are defined in `digital_twin/knowledge/consumer.py` but never reach Loki because the consumer never runs. Filed as **MET-378**.

---

## Reproducer

```bash
# Pre-flight
docker compose --profile observability up -d
# wait for grafana healthy (~30s)
curl -sf http://localhost:3001/api/health   # status 200

# Run
.venv/bin/python scripts/run_tier2_observability_validator_surrogate.py
```

Exit 1 (current state, with MET-376/377/378 outstanding); should be 0 once those land.

---

## Notes on path

The canonical Track-B path is `/uat-cycle12 --tier 2` from a fresh Claude Code session that loads `.mcp.json`. This run used the surrogate; same root-cause gaps will reproduce identically through the canonical path because the gaps are in the gateway HTTP contract + container packaging, not in the MCP transport.

The surrogate is intentionally lightweight: it talks to the gateway over plain HTTP (`urllib`) and to Loki via Grafana's REST proxy. No MCP, no Linear, no agentic decisions — just deterministic checks suitable for CI re-runs.
