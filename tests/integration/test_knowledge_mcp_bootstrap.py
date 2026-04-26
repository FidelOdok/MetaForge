"""Integration test for knowledge MCP adapter bootstrap (MET-335).

Opt in with ``pytest --integration``. Requires the dev
``metaforge-postgres-1`` (with the ``vector`` extension) running on
``localhost:5432`` and the default credentials.

Verifies that ``bootstrap_tool_registry(knowledge_service=...)``
registers the adapter and that ``tool/list`` (via the registry's API)
includes both tools — the wiring contract MET-335 demands.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from digital_twin.knowledge import create_knowledge_service
from tool_registry.bootstrap import bootstrap_tool_registry
from tool_registry.registry import ToolRegistry

pytestmark = pytest.mark.integration


_DEFAULT_DSN = "postgresql://metaforge:metaforge@localhost:5432/metaforge"


def _dsn() -> str:
    return os.environ.get("DATABASE_URL", _DEFAULT_DSN).replace(
        "postgresql+asyncpg://", "postgresql://"
    )


@pytest.fixture
async def knowledge_service(tmp_path: Path) -> AsyncIterator[object]:
    suffix = uuid.uuid4().hex[:8]
    svc = create_knowledge_service(
        "lightrag",
        working_dir=str(tmp_path / f"lightrag-{suffix}"),
        postgres_dsn=_dsn(),
        namespace_prefix=f"lightrag_mcp_{suffix}",
    )
    await svc.initialize()  # type: ignore[attr-defined]
    try:
        yield svc
    finally:
        await svc.close()  # type: ignore[attr-defined]


class TestBootstrap:
    async def test_registers_when_service_provided(self, knowledge_service: object) -> None:
        registry = await bootstrap_tool_registry(
            registry=ToolRegistry(),
            adapter_ids=[],  # disable file-based adapters; only knowledge
            knowledge_service=knowledge_service,
        )
        tool_ids = {t.tool_id for t in registry.list_tools()}
        assert "knowledge.search" in tool_ids
        assert "knowledge.ingest" in tool_ids

    async def test_skips_when_service_none(self) -> None:
        registry = await bootstrap_tool_registry(
            registry=ToolRegistry(),
            adapter_ids=[],
            knowledge_service=None,
        )
        tool_ids = {t.tool_id for t in registry.list_tools()}
        assert "knowledge.search" not in tool_ids
        assert "knowledge.ingest" not in tool_ids


class TestRoundTrip:
    async def test_search_after_ingest_via_mcp_tool(self, knowledge_service: object) -> None:
        """Drive the adapter end-to-end through the MCP handler signatures."""
        from tool_registry.tools.knowledge.adapter import KnowledgeServer

        server = KnowledgeServer(service=knowledge_service)  # type: ignore[arg-type]
        sentinel = (
            f"MCP integration test {uuid.uuid4().hex[:8]}: titanium grade 5 mounting "
            "bracket replaces aluminium 6061 after thermal-cycle failure."
        )
        ingest_result = await server.handle_ingest(
            {
                "content": sentinel,
                "source_path": f"mcp://test/{uuid.uuid4().hex[:8]}.md",
                "knowledge_type": "design_decision",
            }
        )
        assert ingest_result["chunks_indexed"] >= 1
        assert ingest_result["entry_ids"]

        search_result = await server.handle_search(
            {"query": "titanium grade 5 mounting bracket", "top_k": 5}
        )
        hits = search_result["hits"]
        assert hits, "expected at least one MCP search hit"
        # Real cosine score, not a 0.0 stub.
        assert any(h["similarity_score"] > 0 for h in hits)
        # Citation fields round-trip.
        assert any(h["source_path"] is not None for h in hits)
