"""Integration test for re-ingest-after-edit supersede semantics (MET-307).

When the engineer edits a file and re-ingests at the same ``source_path``,
the prior chunks must be retired before the fresh ones are stored — so
search no longer surfaces stale phrases. The implementation hashes the
content (sha256) and compares against ``metadata.content_sha256`` from
the prior chunk's metadata blob. Identical hash ⇒ dedup; different hash
⇒ ``delete_by_source`` then re-chunk, with a structlog
``knowledge_consumer_predelete`` event for the staleness probe.

Skip-clean pattern mirrors ``test_knowledge_project_isolation.py``:
opt in with ``pytest --integration`` and skip when the dev
Postgres+pgvector container isn't reachable on the default DSN.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import structlog
from structlog.testing import capture_logs

from digital_twin.knowledge import create_knowledge_service
from digital_twin.knowledge.lightrag_service import LightRAGKnowledgeService
from digital_twin.knowledge.types import KnowledgeType

pytestmark = pytest.mark.integration


_DEFAULT_DSN = "postgresql://metaforge:metaforge@localhost:5432/metaforge"


def _dsn() -> str:
    return os.environ.get("DATABASE_URL", _DEFAULT_DSN).replace(
        "postgresql+asyncpg://", "postgresql://"
    )


async def _pg_reachable(dsn: str) -> bool:
    """Cheap connectivity probe — 2 s timeout, fail closed.

    Mirrors ``test_knowledge_project_isolation.py``. Avoids sitting on
    LightRAG's ~6 minute internal retry loop when the container is down.
    """
    import asyncpg  # type: ignore[import-untyped]

    try:
        conn = await asyncio.wait_for(asyncpg.connect(dsn), timeout=2.0)
    except (OSError, TimeoutError, asyncpg.PostgresError):
        return False
    except Exception:
        return False
    try:
        await conn.fetchval("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
    finally:
        await conn.close()
    return True


@pytest.fixture
async def service(tmp_path: Path) -> AsyncIterator[LightRAGKnowledgeService]:
    """One-per-test LightRAG service, namespaced to avoid collisions."""
    dsn = _dsn()
    if not await _pg_reachable(dsn):
        pytest.skip(f"Postgres+pgvector not reachable at {dsn} — integration backend unavailable")

    suffix = uuid.uuid4().hex[:8]
    svc = create_knowledge_service(
        "lightrag",
        working_dir=str(tmp_path / f"lightrag-{suffix}"),
        postgres_dsn=dsn,
        namespace_prefix=f"lightrag_supersede_{suffix}",
    )
    await svc.initialize()  # type: ignore[attr-defined]
    try:
        yield svc  # type: ignore[misc]
    finally:
        await svc.close()  # type: ignore[attr-defined]


def _payload_alpha(marker: str) -> str:
    return (
        "# Reingest Edit Sentinel\n\n"
        f"alpha-marker-A1 {marker}: this revision documents the original "
        "decision before the engineer edited the file.\n"
    )


def _payload_beta(marker: str) -> str:
    return (
        "# Reingest Edit Sentinel\n\n"
        f"beta-marker-B1 {marker}: this revision supersedes the alpha "
        "draft after the design review.\n"
    )


class TestReingestAfterEdit:
    """MET-307: identical re-ingest dedups, edited re-ingest supersedes."""

    async def test_reingest_same_content_dedups(self, service: LightRAGKnowledgeService) -> None:
        """Two identical ingests at the same source_path → no re-index, no
        ``knowledge_consumer_predelete`` event."""
        marker = uuid.uuid4().hex[:8]
        source_path = f"reingest://test/{marker}/dedup.md"
        content = _payload_alpha(marker)

        first = await service.ingest(
            content=content,
            source_path=source_path,
            knowledge_type=KnowledgeType.DESIGN_DECISION,
        )
        assert first.chunks_indexed >= 1

        # Re-bind structlog so capture_logs sees the second-ingest events.
        structlog.reset_defaults()
        with capture_logs() as logs:
            second = await service.ingest(
                content=content,
                source_path=source_path,
                knowledge_type=KnowledgeType.DESIGN_DECISION,
            )

        # Identical re-ingest is a dedup: zero new chunks indexed.
        assert second.chunks_indexed == 0, (
            f"identical re-ingest indexed {second.chunks_indexed} chunks — "
            "dedup branch did not fire (MET-307)"
        )
        assert second.entry_ids == []

        # No supersede event should have fired.
        predelete_events = [e for e in logs if e.get("event") == "knowledge_consumer_predelete"]
        assert predelete_events == [], (
            f"identical re-ingest emitted predelete event: {predelete_events}"
        )

    async def test_reingest_changed_content_supersedes(
        self, service: LightRAGKnowledgeService
    ) -> None:
        """Edited content at the same source_path: stale α phrase must be
        retired; fresh β phrase must be searchable."""
        marker = uuid.uuid4().hex[:8]
        source_path = f"reingest://test/{marker}/edit.md"

        await service.ingest(
            content=_payload_alpha(marker),
            source_path=source_path,
            knowledge_type=KnowledgeType.DESIGN_DECISION,
        )
        # Allow LightRAG a moment to flush the first ingest before we
        # supersede — same pattern as the project-isolation test.
        time.sleep(0.5)

        second = await service.ingest(
            content=_payload_beta(marker),
            source_path=source_path,
            knowledge_type=KnowledgeType.DESIGN_DECISION,
        )
        assert second.chunks_indexed >= 1, (
            "edited re-ingest produced zero chunks — supersede branch broke ingest"
        )
        time.sleep(0.5)

        # Search for the α phrase: must be 0 hits (or all below 0.5
        # similarity) at this source_path.
        alpha_hits = await service.search("alpha-marker-A1", top_k=10)
        alpha_at_source = [
            h for h in alpha_hits if h.source_path == source_path and h.similarity_score >= 0.5
        ]
        assert alpha_at_source == [], (
            f"stale α phrase still surfaces after supersede: {alpha_at_source}"
        )

        # Search for the β phrase: must return at least one hit at the
        # same source_path.
        beta_hits = await service.search("beta-marker-B1", top_k=10)
        beta_at_source = [h for h in beta_hits if h.source_path == source_path]
        assert beta_at_source, (
            f"fresh β phrase not searchable after supersede; hits seen: "
            f"{[(h.source_path, h.content[:60]) for h in beta_hits]}"
        )

    async def test_reingest_emits_predelete_log(self, service: LightRAGKnowledgeService) -> None:
        """Supersede branch must emit a structlog
        ``knowledge_consumer_predelete`` event with the source_path and a
        non-zero ``old_chunk_count``."""
        marker = uuid.uuid4().hex[:8]
        source_path = f"reingest://test/{marker}/log.md"

        first = await service.ingest(
            content=_payload_alpha(marker),
            source_path=source_path,
            knowledge_type=KnowledgeType.DESIGN_DECISION,
        )
        assert first.chunks_indexed >= 1

        structlog.reset_defaults()
        with capture_logs() as logs:
            await service.ingest(
                content=_payload_beta(marker),
                source_path=source_path,
                knowledge_type=KnowledgeType.DESIGN_DECISION,
            )

        predelete_events = [e for e in logs if e.get("event") == "knowledge_consumer_predelete"]
        assert len(predelete_events) == 1, (
            f"expected exactly one knowledge_consumer_predelete event, got "
            f"{len(predelete_events)}: {predelete_events}"
        )
        event = predelete_events[0]
        assert event.get("source_path") == source_path, event
        assert event.get("old_chunk_count", 0) > 0, (
            f"expected non-zero old_chunk_count, got {event.get('old_chunk_count')}"
        )
