"""UAT-GOLDEN — critical-path E2E (every layer once, <60s).

The single most important UAT in the suite: if this passes, the
platform's prime contract works end-to-end. If it fails, no other UAT
result matters until it's fixed.

Walks the full stack:

1. **L0 persistence** — Postgres + pgvector reachable.
2. **L1 retrieval** — Real LightRAG-backed ``KnowledgeService`` ingests
   a markdown file and returns it via search.
3. **L2 assembly** — ``ContextAssembler`` wraps the search hits with
   role scope, token budget, and source attribution.
4. **L3 quality** — Conflict / staleness / identity machinery doesn't
   trip on the clean corpus (no false positives).
5. **C2 MCP transport** — Spawns ``python -m metaforge.mcp --transport
   stdio``, the gateway-side ``StdioTransport`` connects, ``tool/list``
   returns ≥7 tools, ``health/check`` reports healthy.

Skipped automatically when Postgres/pgvector aren't reachable so the
suite still runs in environments without the dev backends. Runtime
budget: 60 s.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from digital_twin.context import (
    ContextAssembler,
    ContextAssemblyRequest,
    ContextScope,
)
from digital_twin.knowledge.types import KnowledgeType
from tests.uat.conftest import assert_validates, spawn_metaforge_mcp
from twin_core.api import InMemoryTwinAPI

pytestmark = [pytest.mark.uat, pytest.mark.integration]


GOLDEN_DOC = (
    "MetaForge platform UAT golden-flow marker.\n\n"
    "Digital twin layers: L0 persistence, L1 retrieval, L2 assembly, "
    "L3 quality, L4 extension recipes. The SR-7 mounting bracket is "
    "fabricated from titanium grade 5 after the aluminium 6061 prototype "
    "failed thermal-cycle testing."
)


async def test_uat_golden_critical_path(knowledge_service: object) -> None:
    """End-to-end: Postgres → ingest → search → assemble → MCP round-trip."""
    import warnings

    warnings.filterwarnings(
        "ignore",
        message=".*Event loop is closed.*",
        category=pytest.PytestUnraisableExceptionWarning,
    )

    deadline = 60.0
    start = time.perf_counter()

    # ------------------------------------------------------------------
    # Step 1 — L1 retrieval round-trip through the real LightRAG service
    # ------------------------------------------------------------------
    ingest_result = await knowledge_service.ingest(  # type: ignore[attr-defined]
        content=GOLDEN_DOC,
        source_path="uat://golden/sr7-bracket.md",
        knowledge_type=KnowledgeType.DESIGN_DECISION,
    )
    assert_validates(
        "GOLDEN",
        "L1: ingest returns at least one chunk indexed",
        getattr(ingest_result, "chunks_indexed", 0) >= 1,
        f"chunks_indexed={getattr(ingest_result, 'chunks_indexed', None)}",
    )

    hits = await knowledge_service.search(  # type: ignore[attr-defined]
        query="titanium grade 5 SR-7 bracket",
        top_k=3,
    )
    assert_validates(
        "GOLDEN",
        "L1: search returns the just-ingested doc as a top hit",
        any(getattr(h, "source_path", None) == "uat://golden/sr7-bracket.md" for h in hits),
        f"top hits: {[getattr(h, 'source_path', None) for h in hits]}",
    )

    # ------------------------------------------------------------------
    # Step 2 — L2 assembly carries source attribution + budget
    # ------------------------------------------------------------------
    twin = InMemoryTwinAPI.create()
    assembler = ContextAssembler(twin=twin, knowledge_service=knowledge_service)  # type: ignore[arg-type]
    response = await assembler.assemble(
        ContextAssemblyRequest(
            agent_id="mechanical_agent",
            query="What material does the SR-7 bracket use?",
            scope=[ContextScope.KNOWLEDGE],
            knowledge_top_k=3,
            token_budget=2000,
        )
    )
    assert_validates(
        "GOLDEN",
        "L2: assembler returns at least one fragment for a real query",
        len(response.fragments) >= 1,
        f"got {len(response.fragments)} fragments",
    )
    assert_validates(
        "GOLDEN",
        "L2: every fragment has a source_id (attribution)",
        all(f.source_id for f in response.fragments),
    )
    assert_validates(
        "GOLDEN",
        "L2: token count respects the budget",
        response.token_count <= 2000,
        f"token_count={response.token_count}",
    )

    # ------------------------------------------------------------------
    # Step 3 — L3 quality: clean corpus produces no blocking conflicts
    # ------------------------------------------------------------------
    assert_validates(
        "GOLDEN",
        "L3: clean corpus does not trip BLOCKING conflict (no false positive)",
        response.has_blocking_conflict is False,
        f"conflicts: {[c.field for c in response.conflicts]}",
    )

    # ------------------------------------------------------------------
    # Step 4 — C2 MCP transport: spawn the standalone server, list tools,
    #          health/check round-trip, clean shutdown.
    # ------------------------------------------------------------------
    client, transport = await spawn_metaforge_mcp(adapters="cadquery,calculix")
    try:
        tools = await asyncio.wait_for(client.list_tools(), timeout=20.0)  # type: ignore[attr-defined]
        raw_health = await asyncio.wait_for(
            transport.send('{"jsonrpc":"2.0","id":"h","method":"health/check","params":{}}'),
            timeout=10.0,
        )
        health = json.loads(raw_health)["result"]
        tool_count = len(tools)
    finally:
        await client.disconnect("metaforge")  # type: ignore[attr-defined]
    assert_validates(
        "GOLDEN",
        "C2: tool/list reports ≥7 tools through the spawned subprocess",
        tool_count >= 7,
        f"got {tool_count}",
    )
    assert_validates(
        "GOLDEN",
        "C2: health/check reports status=healthy with ≥1 adapter",
        health.get("status") == "healthy" and health.get("adapter_count", 0) >= 1,
        f"health: {health}",
    )

    # ------------------------------------------------------------------
    # Runtime budget
    # ------------------------------------------------------------------
    elapsed = time.perf_counter() - start
    assert_validates(
        "GOLDEN",
        f"end-to-end runtime under {deadline}s",
        elapsed < deadline,
        f"elapsed={elapsed:.2f}s",
    )
