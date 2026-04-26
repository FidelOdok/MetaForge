"""Integration tests for ``KnowledgeService`` against real Postgres+pgvector.

Opt in with ``pytest --integration``. The default suite skips this file
because the service requires a running ``metaforge-postgres-1`` with the
``vector`` extension installed.

Each test uses a unique ``namespace_prefix`` so concurrent test runs do
not collide in the shared dev database.
"""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from digital_twin.knowledge import (
    IngestResult,
    SearchHit,
    create_knowledge_service,
)
from digital_twin.knowledge.lightrag_service import LightRAGKnowledgeService
from digital_twin.knowledge.types import KnowledgeType

pytestmark = pytest.mark.integration


_DEFAULT_DSN = "postgresql://metaforge:metaforge@localhost:5432/metaforge"
SAMPLE_MD = Path(__file__).resolve().parent.parent / "fixtures" / "knowledge" / "sample.md"


def _dsn() -> str:
    return os.environ.get("DATABASE_URL", _DEFAULT_DSN).replace(
        "postgresql+asyncpg://", "postgresql://"
    )


@pytest.fixture
async def service(tmp_path: Path) -> AsyncIterator[LightRAGKnowledgeService]:
    """One-per-test LightRAG service, namespaced to avoid collisions."""
    suffix = uuid.uuid4().hex[:8]
    svc = create_knowledge_service(
        "lightrag",
        working_dir=str(tmp_path / f"lightrag-{suffix}"),
        postgres_dsn=_dsn(),
        namespace_prefix=f"lightrag_test_{suffix}",
    )
    await svc.initialize()  # type: ignore[attr-defined]
    try:
        yield svc  # type: ignore[misc]
    finally:
        await svc.close()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------


class TestIngest:
    async def test_ingest_returns_chunk_count(self, service: LightRAGKnowledgeService) -> None:
        content = SAMPLE_MD.read_text(encoding="utf-8")
        result = await service.ingest(
            content=content,
            source_path=str(SAMPLE_MD),
            knowledge_type=KnowledgeType.DESIGN_DECISION,
        )
        assert isinstance(result, IngestResult)
        assert result.chunks_indexed >= 1
        assert result.source_path == str(SAMPLE_MD)
        assert len(result.entry_ids) == result.chunks_indexed


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


class TestSearch:
    async def test_search_after_ingest_returns_hit(self, service: LightRAGKnowledgeService) -> None:
        content = SAMPLE_MD.read_text(encoding="utf-8")
        await service.ingest(
            content=content,
            source_path=str(SAMPLE_MD),
            knowledge_type=KnowledgeType.DESIGN_DECISION,
        )
        # Allow LightRAG a moment to flush async writes.
        time.sleep(0.5)
        hits = await service.search("titanium grade 5 bracket", top_k=5)
        assert hits, "expected at least one hit"
        assert isinstance(hits[0], SearchHit)
        assert any(h.similarity_score > 0 for h in hits)

    async def test_citation_metadata_round_trips(self, service: LightRAGKnowledgeService) -> None:
        content = SAMPLE_MD.read_text(encoding="utf-8")
        await service.ingest(
            content=content,
            source_path=str(SAMPLE_MD),
            knowledge_type=KnowledgeType.DESIGN_DECISION,
        )
        time.sleep(0.5)
        hits = await service.search("bracket material decision", top_k=5)
        assert hits
        hit = hits[0]
        assert hit.source_path == str(SAMPLE_MD)
        assert hit.heading is not None
        assert hit.chunk_index is not None
        assert hit.total_chunks is not None
        assert hit.chunk_index < hit.total_chunks

    async def test_knowledge_type_filter(self, service: LightRAGKnowledgeService) -> None:
        # LightRAG dedupes by content hash, so two ingests with
        # identical content collapse — even under different knowledge
        # types. Distinct content per file keeps both rows alive so
        # the type filter has something to discriminate.
        decision_content = (
            "# Bracket Material Decision\n\n"
            "We choose titanium grade 5 for the SR-7 mounting bracket. "
            "Approved 2026-04-12 by mechanical lead.\n"
        )
        failure_content = (
            "# Pull-Out Failure Log\n\n"
            "Heat-set inserts in 6061-T6 bracket relaxed after 200 thermal "
            "cycles. Root cause: aluminium creep above proof stress.\n"
        )
        await service.ingest(
            content=decision_content,
            source_path="/tmp/decision.md",
            knowledge_type=KnowledgeType.DESIGN_DECISION,
        )
        await service.ingest(
            content=failure_content,
            source_path="/tmp/failure.md",
            knowledge_type=KnowledgeType.FAILURE,
        )
        time.sleep(0.5)
        decision_hits = await service.search(
            "bracket material",
            top_k=10,
            knowledge_type=KnowledgeType.DESIGN_DECISION,
        )
        assert decision_hits
        assert all(h.knowledge_type == KnowledgeType.DESIGN_DECISION for h in decision_hits)


# ---------------------------------------------------------------------------
# Delete + dedup
# ---------------------------------------------------------------------------


class TestDelete:
    async def test_reingest_same_source_dedupes(self, service: LightRAGKnowledgeService) -> None:
        content = SAMPLE_MD.read_text(encoding="utf-8")
        first = await service.ingest(
            content=content,
            source_path=str(SAMPLE_MD),
            knowledge_type=KnowledgeType.DESIGN_DECISION,
        )
        second = await service.ingest(
            content=content,
            source_path=str(SAMPLE_MD),
            knowledge_type=KnowledgeType.DESIGN_DECISION,
        )
        # Same id-set means dedup at the storage level — the second call
        # may report the same chunk count, but the deletable count must
        # match the single-ingest baseline.
        assert second.chunks_indexed == first.chunks_indexed
        deleted = await service.delete_by_source(str(SAMPLE_MD))
        assert deleted == first.chunks_indexed

    async def test_delete_by_source_removes_chunks(self, service: LightRAGKnowledgeService) -> None:
        content = SAMPLE_MD.read_text(encoding="utf-8")
        await service.ingest(
            content=content,
            source_path=str(SAMPLE_MD),
            knowledge_type=KnowledgeType.DESIGN_DECISION,
        )
        time.sleep(0.5)
        before = await service.search("titanium bracket", top_k=5)
        assert before
        deleted = await service.delete_by_source(str(SAMPLE_MD))
        assert deleted >= 1
        time.sleep(0.5)
        after = await service.search("titanium bracket", top_k=5)
        # After delete, no hit should still resolve to this source.
        assert not any(h.source_path == str(SAMPLE_MD) for h in after)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


class TestHealth:
    async def test_health_check_reports_pgvector(self, service: LightRAGKnowledgeService) -> None:
        report = await service.health_check()
        assert report["status"] == "ok"
        assert report["backend"] == "lightrag"
        assert report["pgvector"] is True
