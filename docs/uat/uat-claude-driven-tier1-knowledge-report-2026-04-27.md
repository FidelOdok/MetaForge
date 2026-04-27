# UAT Tier-1 Knowledge — Claude-driven first run (2026-04-27)

**Scenario set**: `tests/uat/scenarios/tier1/knowledge.md` (8 scenarios)
**Validates**: MET-346, MET-293, MET-307, MET-335, MET-336
**Tier**: 1 (cycle gate cadence)
**Path**: validator surrogate (parent Claude Code session pre-dates `.mcp.json`; canonical path is `/uat-cycle12 --tier 1`)
**Surrogate driver**: `scripts/run_tier1_knowledge_validator_surrogate.py`
**Elapsed**: 115.18s wall (incl. sentence-transformers cold load)
**Overall verdict**: **FAIL** — 14 of 15 assertions PASS, 1 FAIL surfacing one real gap

---

## Summary

| # | Scenario | Verdict | Notes |
|---|----------|---------|-------|
| 1 | ingest then search round-trip | PASS (3/3) | content + source_path round-trip; "pgvector"/"LightRAG" surface in hit |
| 2 | ingest classifies by knowledge_type | PASS (2/2) | filter narrows to `component`; no failure-typed leaks |
| 3 | empty search produces deterministic empty list | PASS (2/2) | nonsense query returns 0 hits, no crash |
| 4 | search respects top_k cap | PASS (1/1) | broad `MetaForge` query honoured `top_k=2` |
| 5 | knowledge.search response carries citation fields | PASS (4/4) | `chunk_index=0`, `total_chunks=1` populated, source_path non-empty |
| 6 | knowledge.ingest rejects empty content cleanly | **FAIL** | silent success: `chunks_indexed=0`, no exception → MET-3XX (filed below) |
| 7 | deduplication on identical re-ingest | PASS (1/1) | re-ingest dropped prior chunks; one hit at the source_path |
| 8 | forge ingest equivalent — directory walk | PASS (1/1) | both walker source_paths in top 5 |

---

## FAIL — Scenario 6: knowledge.ingest rejects empty content cleanly

**Validates**: MET-346

**Request**:
```json
{
  "content": "",
  "source_path": "uat://tier1/knowledge/empty-rejection",
  "knowledge_type": "design_decision"
}
```

**Actual response**:
```json
{
  "chunks_indexed": 0,
  "source_path": "uat://tier1/knowledge/empty-rejection"
}
```

The `LightRAGKnowledgeService` recognises the empty input — its
structured log emits `lightrag_ingest_empty source_path=...` —
but returns an `IngestResult` with `chunks_indexed=0` and `entry_ids=[]`
instead of raising or surfacing an explicit failure status.

This is **the exact failure mode the scenario was written to detect**.
From `tests/uat/scenarios/tier1/knowledge.md` lines 132-134:

> It must NOT silently succeed with `chunks_indexed=0` — that's the
> failure mode this scenario exists to detect.

**Why it matters**: an MCP-tool caller (Claude) that passes empty
content gets `status="success"` back and a 0-chunk result. The agent
has no signal to retry with non-empty content; it will treat the
ingest as completed. This conflicts with the contract that
`knowledge.ingest` reports failures in-band.

**Fix direction**: in `LightRAGKnowledgeService.ingest()`, after the
`lightrag_ingest_empty` log line, raise `ValueError("content is empty
or whitespace")` (or return an `IngestResult` shape with an explicit
`status="failure"` field if the dataclass supports it). The wire
adapter (`mcp_core` knowledge bridge) should map the raise to
JSON-RPC error -32001 with `data.details` mentioning empty content.

**Filed forward**: MET-3XX (UAT FAIL: knowledge.ingest silently
returns chunks_indexed=0 on empty content) — see Linear, parent
MET-369, P1.15 priority.

---

## PASS scenarios — evidence

### Scenario 1: ingest then search round-trip
- ingest → `chunks_indexed=1` for `uat://tier1/knowledge/round-trip` (167 ms)
- search `"pgvector LightRAG persistence"` top_k=5 → 1 hit, score 0.635, content "metaforge tier-1 marker: dependable persistence layer using postgres + pgvector under the lightrag adapter."

### Scenario 2: ingest classifies by knowledge_type
- two ingests across `failure-mode` (FAILURE) and `component` (COMPONENT) source_paths
- search `"titanium"` top_k=5 with `knowledge_type=COMPONENT` filter → 1 hit on `uat://tier1/knowledge/component` (no failure-typed leak)

### Scenario 3: empty search produces deterministic empty list
- search `"xyz-uat-marker-no-match-zzzzzzz"` top_k=3 → 0 hits, empty score list (deterministic empty)

### Scenario 4: search respects top_k cap
- search `"MetaForge"` top_k=2 → 1 hit (≤ cap)

### Scenario 5: knowledge.search response carries citation fields
- ingest at `uat://tier1/knowledge/citation`
- search `"citation field probe"` top_k=1 → 1 hit with `chunk_index=0`, `total_chunks=1`, non-empty source_path. Heading is `null` (acceptable — single-chunk doc, scenario explicitly allows this).

### Scenario 7: deduplication on identical re-ingest
- two identical ingests at same source_path
- search `"unique-token-q9z"` top_k=10 → exactly 1 matching hit (pre-delete worked)

### Scenario 8: forge ingest equivalent — directory walk
- ingests at `uat://tier1/knowledge/walker/file-1.md` + `file-2.md`
- search `"persistence retrieval layer"` top_k=5 → both walker paths in top 3 hits

---

## How to reproduce

```bash
# from repo root, with .venv created:
.venv/bin/pip install -e '.[dev,knowledge]'   # one-time
docker compose up -d postgres                 # if not already up
.venv/bin/python scripts/run_tier1_knowledge_validator_surrogate.py
```

Exit 0 = all PASS; exit 1 = at least one FAIL (per current run).

---

## Notes on path

The canonical Track-B path for this scenario set is:

```
/uat-cycle12 --tier 1 --only "tier1/knowledge"
```

That spawns the `uat-validator` subagent with `mcp__metaforge__*`
tools auto-loaded from `.mcp.json`. This run used the surrogate
because the parent Claude Code session was started before PR #132
landed `.mcp.json`, so it never registered the metaforge MCP server.
A fresh session will use the canonical path automatically.

The surrogate exercises the **same `KnowledgeService` interface** the
MCP `knowledge.ingest` / `knowledge.search` tools wrap, so the FAIL
identified here is a contract gap, not a transport quirk — it will
reproduce identically through the canonical path.
