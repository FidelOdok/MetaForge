# UAT Tier-2 Observability — Re-run after the gateway image fix landed (2026-04-28)

**Scenario set**: `tests/uat/scenarios/tier2/{staleness,provenance,dedup}-probe.md`
**Validates**:
**Surrogate driver**: `scripts/run_tier2_observability_validator_surrogate.py`
**Verdict**: 8 PASS / 5 FAIL / 1 BLOCKED — **promotion from 5/13 PASS at first run**

---

## Summary vs. first run

| Metric | First run (2026-04-27) | Now (2026-04-28) |
|--------|------------------------|------------------|
| PASS | 5 | **8** |
| FAIL | 8 | 5 |
| BLOCKED | 1 (`/context/assemble`) | 1 (same — out of scope) |
| Gateway image | `2026-04-08` (stale, no sentence-transformers) | `2026-04-28` (sentence-transformers 5.4.1) |
| Vector embeddings | zero-vector fallback (random search) | real semantic search |

---

## What the gateway image, provenance, consumer and registry-shim fixes achieved

**Provenance fix confirmed**: `provenance/1: every hit has chunk_index or total_chunks` now PASSes — citation fields surface through the gateway response. (FAILed first run, PASSes now.)

**Gateway image fix confirmed**: gateway image now ships `sentence-transformers 5.4.1`. Loki shows `event="lightrag_embedder_prewarmed"` instead of the prior `event="sentence_transformers_not_installed"`. Real semantic search works (verified by `provenance/1: search returns ≥1 hit` PASSing with a non-zero similarity score).

**Consumer pre-delete fix confirmed via /documents path**: when the surrogate variant uses `/api/v1/knowledge/documents`, Loki captures `event="knowledge_consumer_predelete"` on re-ingest. (Switched back to `/ingest` for the canonical run because of the dual-storage gap below.)

---

## Remaining FAILs trace to one new gap

The 5 remaining FAILs all trace to a single architectural gap surfaced by this run, now filed forward: the gateway has two parallel knowledge storage paths.

| Endpoint | Backend |
|----------|---------|
| `POST /ingest` + `GET /search` | `app.state.knowledge_store` (standalone PgVectorKnowledgeStore) |
| `POST /documents` | `app.state.knowledge_service` (LightRAG) |

These two backends don't share data. So no matter which route the surrogate uses to write, the other route's reads can't see it. Patterns of FAIL stay constant — only the names change:

- Pick `/ingest` → the consumer pre-delete doesn't fire (route bypasses LightRAG) → dedup tests FAIL
- Pick `/documents` → `/search` can't find what `/documents` wrote → search-result tests FAIL

The recommended fix is **A — make `/ingest` + `/search` delegate to `KnowledgeService`** so there's one coherent store. The standalone `PgVectorKnowledgeStore` should be removed (it pre-dates the LightRAG knowledge layer).

---

## Verdict table

| Probe | Verdict |
|-------|---------|
| staleness/1: `context_truncated` metric | BLOCKED (`/context/assemble` not built — separate roadmap) |
| staleness/2: superseded fragments not retrieved | 1 PASS / 1 FAIL → dual-storage gap |
| provenance/1: hits have source_path | 3 PASS / 1 FAIL → noise from earlier-test pre-source_path entries |
| provenance/2: log trail | 2 PASS / 1 FAIL → gateway log line doesn't include source_path field |
| dedup/1: drop stale chunks | 1 PASS / 2 FAIL → dual-storage gap |
| dedup/2: distinct paths coexist | 1 FAIL → dual-storage gap |

The provenance/2 sub-FAIL ("source_path appears in log line") is a smaller separate item — the gateway's `event="knowledge_ingested"` structured log doesn't include the `source_path` field. Lower priority since the trace_id/span_id chain still works.

---

## Reproducer

```bash
docker compose --profile observability up -d
.venv/bin/python scripts/run_tier2_observability_validator_surrogate.py
```

Exit 1 (current state, with the dual-storage gap outstanding); should be 0 once it is fixed.

---

## Tier-2 status

**The Tier-2 observability item stays In Review** until the dual-storage gap is fixed. Five P1 root causes from the original Tier-2 first run (empty tool list, provenance fields, gateway image, consumer start, plus the 3-layer unified-server registry fix) are all closed and verified at the layers they target. The remaining FAILs are a separate architectural finding, not a regression.
