"""Shared test doubles for MCP inventory regression tests (MET-433).

A ``StubKnowledgeService`` that satisfies the runtime-checkable
``KnowledgeService`` Protocol with cheap no-op implementations. Used by
``test_mcp_tools_inventory`` so the regression suite can wire the
knowledge adapter into ``build_unified_server`` without spinning up a
real LightRAG pgvector backend.

Lives outside ``conftest.py`` so it's an explicit import (the inventory
file only needs it for one test).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from digital_twin.knowledge.service import (
    ExtractedProperties,
    IngestResult,
    SearchHit,
    SourceSummary,
)
from digital_twin.knowledge.types import KnowledgeType


class StubKnowledgeService:
    """Bare-minimum ``KnowledgeService`` for adapter-registration tests."""

    async def ingest(
        self,
        content: str,
        source_path: str,
        knowledge_type: KnowledgeType,
        source_work_product_id: UUID | None = None,
        metadata: dict[str, Any] | None = None,
        project_id: UUID | None = None,
        actor_id: str | None = None,
    ) -> IngestResult:
        return IngestResult(entry_ids=[], chunks_indexed=0, source_path=source_path)

    async def search(
        self,
        query: str,
        top_k: int = 5,
        knowledge_type: KnowledgeType | None = None,
        filters: dict[str, Any] | None = None,
        project_id: UUID | None = None,
        rerank: bool = False,
        actor_id: str | None = None,
        include_historical: bool = False,
    ) -> list[SearchHit]:
        return []

    async def delete_by_source(
        self,
        source_path: str,
        project_id: UUID | None = None,
    ) -> int:
        return 0

    async def list_sources(
        self,
        project_id: UUID | None = None,
        knowledge_type: KnowledgeType | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SourceSummary]:
        return []

    async def extract_properties(
        self,
        mpn: str,
        properties: list[str],
        *,
        aliases: dict[str, list[str]] | None = None,
    ) -> ExtractedProperties:
        return ExtractedProperties(
            mpn=mpn,
            mpn_found=False,
            datasheet_revision=None,
            datasheet_published_at=None,
            datasheet_source_path=None,
            items=[],
        )

    async def health_check(self) -> dict[str, Any]:
        return {"status": "ok", "backend": "stub"}
