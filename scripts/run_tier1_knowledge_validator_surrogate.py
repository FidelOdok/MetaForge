"""Run the Tier-1 ``knowledge.*`` UAT scenarios as the
``uat-validator`` subagent would, capturing per-step request/response
evidence and writing a markdown report.

This is the validator surrogate path — used when the parent Claude
Code session doesn't have the metaforge MCP server loaded via
``.mcp.json`` (e.g. session was started before PR #132 landed). The
canonical path is ``/uat-cycle12 --tier 1`` which spawns the subagent
with ``mcp__metaforge__*`` tools available.

All 8 scenarios in ``tests/uat/scenarios/tier1/knowledge.md`` share one
fresh ``LightRAGKnowledgeService`` instance — that mirrors a single
subagent invocation walking the file top-to-bottom, and lets the
``top_k`` cap scenario rely on the round-trip + classification
scenarios as setup (per the markdown's own "Given").
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import uuid
from pathlib import Path

REPO_ROOT = Path("/mnt/c/Users/odokf/Documents/MetaForge")
sys.path.insert(0, str(REPO_ROOT))


async def main() -> int:
    from digital_twin.knowledge import create_knowledge_service
    from digital_twin.knowledge.types import KnowledgeType

    evidence: list[dict] = []
    verdicts: list[dict] = []
    start_total = time.perf_counter()
    overall_status = "PASS"

    def record(scenario: str, step: str, request: dict, response: dict | str, duration_ms: float):
        evidence.append(
            {
                "scenario": scenario,
                "step": step,
                "request": request,
                "response": response,
                "duration_ms": round(duration_ms, 1),
            }
        )

    def then(scenario: str, label: str, condition: bool, detail: str = ""):
        nonlocal overall_status
        verdict = "PASS" if condition else "FAIL"
        if not condition:
            overall_status = "FAIL"
        verdicts.append(
            {"scenario": scenario, "then": label, "verdict": verdict, "detail": detail}
        )

    svc_suffix = uuid.uuid4().hex[:8]
    svc = create_knowledge_service(
        "lightrag",
        working_dir=f"/tmp/lightrag-tier1-validator-{svc_suffix}",
        postgres_dsn="postgresql://metaforge:metaforge@localhost:5432/metaforge",
        namespace_prefix=f"lightrag_tier1_{svc_suffix}",
    )
    await svc.initialize()
    try:
        # --------------------------------------------------------------
        # Scenario 1: ingest then search round-trip
        # --------------------------------------------------------------
        scen = "1: ingest then search round-trip"
        s1_path = "uat://tier1/knowledge/round-trip"
        s1_content = (
            "MetaForge tier-1 marker: dependable persistence layer using "
            "Postgres + pgvector under the LightRAG adapter."
        )
        t0 = time.perf_counter()
        s1_ingest = await svc.ingest(
            content=s1_content,
            source_path=s1_path,
            knowledge_type=KnowledgeType.DESIGN_DECISION,
        )
        record(
            scen,
            "ingest",
            {"content": s1_content, "source_path": s1_path, "knowledge_type": "design_decision"},
            {
                "chunks_indexed": s1_ingest.chunks_indexed,
                "source_path": s1_ingest.source_path,
            },
            (time.perf_counter() - t0) * 1000,
        )
        t0 = time.perf_counter()
        s1_hits = await svc.search(query="pgvector LightRAG persistence", top_k=5)
        record(
            scen,
            "search",
            {"query": "pgvector LightRAG persistence", "top_k": 5},
            {
                "hit_count": len(s1_hits),
                "hits": [
                    {
                        "source_path": h.source_path,
                        "similarity_score": round(h.similarity_score, 3),
                        "content_preview": (h.content or "")[:120],
                    }
                    for h in s1_hits
                ],
            },
            (time.perf_counter() - t0) * 1000,
        )
        then(scen, "ingest returns chunks_indexed >= 1", s1_ingest.chunks_indexed >= 1,
             f"chunks_indexed={s1_ingest.chunks_indexed}")
        s1_match = next((h for h in s1_hits if h.source_path == s1_path), None)
        then(scen, "≥1 hit has source_path == round-trip path", s1_match is not None,
             f"hits={[h.source_path for h in s1_hits]}")
        s1_text = (s1_match.content or "").lower() if s1_match else ""
        then(scen, "matching hit content contains 'pgvector' or 'LightRAG'",
             "pgvector" in s1_text or "lightrag" in s1_text,
             f"content_preview={s1_text[:200]!r}")

        # --------------------------------------------------------------
        # Scenario 2: ingest classifies by knowledge_type
        # --------------------------------------------------------------
        scen = "2: ingest classifies by knowledge_type"
        s2a_path = "uat://tier1/knowledge/failure-mode"
        s2b_path = "uat://tier1/knowledge/component"
        await svc.ingest(
            content="Failure: thermal cycling broke the aluminium 6061 mount",
            source_path=s2a_path,
            knowledge_type=KnowledgeType.FAILURE,
        )
        await svc.ingest(
            content="Component: titanium grade 5 sheet, 2mm thick",
            source_path=s2b_path,
            knowledge_type=KnowledgeType.COMPONENT,
        )
        t0 = time.perf_counter()
        s2_hits = await svc.search(
            query="titanium",
            top_k=5,
            knowledge_type=KnowledgeType.COMPONENT,
        )
        record(
            scen,
            "search with knowledge_type=component filter",
            {"query": "titanium", "top_k": 5, "knowledge_type": "component"},
            {
                "hit_count": len(s2_hits),
                "hits": [
                    {
                        "source_path": h.source_path,
                        "knowledge_type": str(h.knowledge_type) if h.knowledge_type else None,
                        "similarity_score": round(h.similarity_score, 3),
                    }
                    for h in s2_hits
                ],
            },
            (time.perf_counter() - t0) * 1000,
        )
        then(scen, "filtered search returns ≥1 hit", len(s2_hits) >= 1,
             f"hit_count={len(s2_hits)}")
        leaks = [
            h for h in s2_hits
            if h.knowledge_type is not None and h.knowledge_type != KnowledgeType.COMPONENT
        ]
        then(scen, "no failure-typed leaks", len(leaks) == 0,
             f"leaked hits={[(h.source_path, str(h.knowledge_type)) for h in leaks]}")

        # --------------------------------------------------------------
        # Scenario 3: empty search produces deterministic empty list
        # --------------------------------------------------------------
        scen = "3: empty search produces deterministic empty list"
        nonsense = "xyz-uat-marker-no-match-zzzzzzz"
        t0 = time.perf_counter()
        s3_hits = await svc.search(query=nonsense, top_k=3)
        record(
            scen,
            "search nonsense token",
            {"query": nonsense, "top_k": 3},
            {
                "hit_count": len(s3_hits),
                "scores": [round(h.similarity_score, 3) for h in s3_hits],
            },
            (time.perf_counter() - t0) * 1000,
        )
        then(scen, "response is a list", isinstance(s3_hits, list),
             f"type={type(s3_hits).__name__}")
        deterministic_no_match = (
            len(s3_hits) == 0
            or all(h.similarity_score < 0.5 for h in s3_hits)
        )
        then(scen, "either empty or all hits below 0.5 confidence",
             deterministic_no_match,
             f"scores={[round(h.similarity_score, 3) for h in s3_hits]}")

        # --------------------------------------------------------------
        # Scenario 4: search respects top_k cap
        # --------------------------------------------------------------
        scen = "4: search respects top_k cap"
        t0 = time.perf_counter()
        s4_hits = await svc.search(query="MetaForge", top_k=2)
        record(
            scen,
            "broad search top_k=2",
            {"query": "MetaForge", "top_k": 2},
            {
                "hit_count": len(s4_hits),
                "hits": [{"source_path": h.source_path} for h in s4_hits],
            },
            (time.perf_counter() - t0) * 1000,
        )
        then(scen, "hit_count <= top_k", len(s4_hits) <= 2,
             f"hit_count={len(s4_hits)}")

        # --------------------------------------------------------------
        # Scenario 5: knowledge.search response carries citation fields
        # --------------------------------------------------------------
        scen = "5: knowledge.search response carries citation fields"
        s5_path = "uat://tier1/knowledge/citation"
        await svc.ingest(
            content="Citation field probe — heading 'API' / chunk 0.",
            source_path=s5_path,
            knowledge_type=KnowledgeType.DESIGN_DECISION,
        )
        t0 = time.perf_counter()
        s5_hits = await svc.search(query="citation field probe", top_k=1)
        record(
            scen,
            "search citation probe",
            {"query": "citation field probe", "top_k": 1},
            {
                "hit_count": len(s5_hits),
                "hits": [
                    {
                        "source_path": h.source_path,
                        "chunk_index": h.chunk_index,
                        "total_chunks": h.total_chunks,
                        "heading": h.heading,
                    }
                    for h in s5_hits
                ],
            },
            (time.perf_counter() - t0) * 1000,
        )
        then(scen, "exactly 1 hit", len(s5_hits) == 1,
             f"hit_count={len(s5_hits)}")
        if s5_hits:
            h = s5_hits[0]
            then(scen, "non-empty source_path", bool(h.source_path),
                 f"source_path={h.source_path!r}")
            then(scen, "chunk_index field present (not None)",
                 h.chunk_index is not None,
                 f"chunk_index={h.chunk_index!r}")
            then(scen, "total_chunks field present (not None)",
                 h.total_chunks is not None,
                 f"total_chunks={h.total_chunks!r}")

        # --------------------------------------------------------------
        # Scenario 6: knowledge.ingest rejects empty content cleanly
        # --------------------------------------------------------------
        scen = "6: knowledge.ingest rejects empty content cleanly"
        s6_path = "uat://tier1/knowledge/empty-rejection"
        t0 = time.perf_counter()
        s6_response: dict
        s6_classification: str
        try:
            s6_result = await svc.ingest(
                content="",
                source_path=s6_path,
                knowledge_type=KnowledgeType.DESIGN_DECISION,
            )
            chunks = s6_result.chunks_indexed
            s6_response = {
                "chunks_indexed": chunks,
                "source_path": s6_result.source_path,
            }
            if chunks == 0:
                s6_classification = "silent-success-zero-chunks"
            else:
                s6_classification = "silent-success-nonzero-chunks"
        except Exception as exc:  # noqa: BLE001
            s6_response = {
                "raised": True,
                "exception_type": type(exc).__name__,
                "message": str(exc),
            }
            s6_classification = "raised-exception"
        record(
            scen,
            "ingest empty content",
            {"content": "", "source_path": s6_path},
            s6_response,
            (time.perf_counter() - t0) * 1000,
        )
        # PASS conditions: either explicit failure status OR raised
        # exception. FAIL: silent success with chunks_indexed=0.
        passed = s6_classification == "raised-exception"
        then(scen, "rejects empty content (raise OR failure status)", passed,
             f"classification={s6_classification}; response={s6_response}")

        # --------------------------------------------------------------
        # Scenario 7: deduplication on identical re-ingest
        # --------------------------------------------------------------
        scen = "7: deduplication on identical re-ingest"
        s7_path = "uat://tier1/knowledge/dedup-probe"
        s7_content = "Dedup probe content unique-token-q9z."
        await svc.ingest(
            content=s7_content,
            source_path=s7_path,
            knowledge_type=KnowledgeType.DESIGN_DECISION,
        )
        await svc.ingest(
            content=s7_content,
            source_path=s7_path,
            knowledge_type=KnowledgeType.DESIGN_DECISION,
        )
        t0 = time.perf_counter()
        s7_hits = await svc.search(query="unique-token-q9z", top_k=10)
        record(
            scen,
            "search dedup token",
            {"query": "unique-token-q9z", "top_k": 10},
            {
                "hit_count": len(s7_hits),
                "hits": [
                    {
                        "source_path": h.source_path,
                        "content_preview": (h.content or "")[:120],
                    }
                    for h in s7_hits
                ],
            },
            (time.perf_counter() - t0) * 1000,
        )
        s7_matches = [h for h in s7_hits if h.source_path == s7_path]
        then(scen, "exactly one hit (deduplicated)", len(s7_matches) == 1,
             f"matching hit_count={len(s7_matches)}")

        # --------------------------------------------------------------
        # Scenario 8: forge ingest equivalent — directory walk
        # --------------------------------------------------------------
        scen = "8: forge ingest equivalent — directory walk"
        s8a_path = "uat://tier1/knowledge/walker/file-1.md"
        s8b_path = "uat://tier1/knowledge/walker/file-2.md"
        await svc.ingest(
            content="Walker file 1 — covers the L0 persistence layer.",
            source_path=s8a_path,
            knowledge_type=KnowledgeType.DESIGN_DECISION,
        )
        await svc.ingest(
            content="Walker file 2 — covers the L1 retrieval layer.",
            source_path=s8b_path,
            knowledge_type=KnowledgeType.DESIGN_DECISION,
        )
        t0 = time.perf_counter()
        s8_hits = await svc.search(query="persistence retrieval layer", top_k=5)
        record(
            scen,
            "search across walker files",
            {"query": "persistence retrieval layer", "top_k": 5},
            {
                "hit_count": len(s8_hits),
                "hits": [{"source_path": h.source_path} for h in s8_hits],
            },
            (time.perf_counter() - t0) * 1000,
        )
        s8_paths = {h.source_path for h in s8_hits}
        then(scen, "both walker source_paths in top 5",
             {s8a_path, s8b_path}.issubset(s8_paths),
             f"hit source_paths={sorted(p for p in s8_paths if p)}")

    finally:
        await svc.close()

    elapsed = time.perf_counter() - start_total
    output = {
        "scenario_set": "tests/uat/scenarios/tier1/knowledge.md",
        "validates": ["MET-346", "MET-293", "MET-307", "MET-335", "MET-336"],
        "tier": 1,
        "verdict": overall_status,
        "evidence": evidence,
        "verdicts": verdicts,
        "elapsed_seconds": round(elapsed, 2),
    }
    print(json.dumps(output, indent=2, default=str))
    return 0 if overall_status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
