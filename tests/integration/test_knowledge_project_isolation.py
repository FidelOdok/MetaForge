"""Integration test for project isolation enforcement (MET-401).

Asserts that ``LightRAGKnowledgeService`` actually keeps project A's
documents out of project B's searches end-to-end (via real Postgres +
pgvector). The fixture pattern mirrors ``test_knowledge_service.py`` —
opt in with ``pytest --integration``.

Three cases are covered:

1. Ingest content tagged ``project_id=P_A``.
2. Search the same query under ``project_id=P_A`` -> at least one hit.
3. Search under ``project_id=P_B`` -> exactly zero hits.

If the dev Postgres+pgvector container isn't reachable on the default
DSN, the suite is skipped with a clear reason (``--integration`` is
already gated on infra availability per ``tests/conftest.py``).
"""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID

import pytest

from digital_twin.knowledge import create_knowledge_service
from digital_twin.knowledge.lightrag_service import LightRAGKnowledgeService
from digital_twin.knowledge.types import KnowledgeType

pytestmark = pytest.mark.integration


_DEFAULT_DSN = "postgresql://metaforge:metaforge@localhost:5432/metaforge"

# Stable UUIDs so failures are easier to read in logs.
PROJECT_A = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
PROJECT_B = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")


def _dsn() -> str:
    return os.environ.get("DATABASE_URL", _DEFAULT_DSN).replace(
        "postgresql+asyncpg://", "postgresql://"
    )


async def _pg_reachable(dsn: str) -> bool:
    """Cheap connectivity probe so we can SKIP cleanly instead of ERROR'ing
    when the dev Postgres+pgvector container isn't running locally.

    Uses a 2 s timeout — long enough for a healthy dev container, short
    enough that the suite doesn't sit on a 6-minute LightRAG retry loop
    (which is what happens if we let ``LightRAGKnowledgeService.initialize``
    discover the failure itself).
    """
    import asyncio

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
    """One-per-test LightRAG service, namespaced to avoid collisions.

    Skips cleanly if Postgres+pgvector isn't reachable (per MET-401's
    "skip cleanly when integration backend unavailable" requirement).
    """
    dsn = _dsn()
    if not await _pg_reachable(dsn):
        pytest.skip(f"Postgres+pgvector not reachable at {dsn} — integration backend unavailable")

    suffix = uuid.uuid4().hex[:8]
    svc = create_knowledge_service(
        "lightrag",
        working_dir=str(tmp_path / f"lightrag-{suffix}"),
        postgres_dsn=dsn,
        namespace_prefix=f"lightrag_iso_{suffix}",
    )
    await svc.initialize()  # type: ignore[attr-defined]
    try:
        yield svc  # type: ignore[misc]
    finally:
        await svc.close()  # type: ignore[attr-defined]


class TestProjectIsolation:
    """MET-401: documents under project A do not leak into project B searches."""

    async def test_search_scopes_to_project(self, service: LightRAGKnowledgeService) -> None:
        # Distinctive content so we know it's our chunk, not a stray hit.
        sentinel = (
            "# Project Isolation Sentinel\n\n"
            f"MET-401 isolation marker {uuid.uuid4().hex[:8]}: "
            "titanium grade 5 mounting bracket replaces aluminium 6061 "
            "after thermal-cycle failure.\n"
        )
        source_path = f"iso://test/{uuid.uuid4().hex[:8]}.md"

        # 1. Ingest under PROJECT_A
        ingest = await service.ingest(
            content=sentinel,
            source_path=source_path,
            knowledge_type=KnowledgeType.DESIGN_DECISION,
            project_id=PROJECT_A,
        )
        assert ingest.chunks_indexed >= 1
        # The metadata we round-trip should carry the project_id stamp.
        # (Direct read happens inside the service; we assert via search.)

        # Allow LightRAG a moment to flush async writes.
        time.sleep(0.5)

        # 2. Search under PROJECT_A -> at least one hit, and it must
        # belong to our project (not a leaker from another tenant).
        hits_a = await service.search(
            "titanium grade 5 mounting bracket",
            top_k=10,
            project_id=PROJECT_A,
        )
        assert hits_a, "expected at least one hit under PROJECT_A"
        assert any(h.source_path == source_path for h in hits_a)
        assert all(h.metadata.get("project_id") == str(PROJECT_A) for h in hits_a), (
            "every PROJECT_A hit must carry the PROJECT_A stamp"
        )

        # 3. Search under PROJECT_B -> zero hits for this source.
        hits_b = await service.search(
            "titanium grade 5 mounting bracket",
            top_k=10,
            project_id=PROJECT_B,
        )
        assert not any(h.source_path == source_path for h in hits_b), (
            "PROJECT_A document leaked into PROJECT_B search results — "
            "isolation contract violated (MET-401)"
        )

    async def test_delete_by_source_respects_project_scope(
        self, service: LightRAGKnowledgeService
    ) -> None:
        """``delete_by_source(source_path, project_id=A)`` must only retire
        chunks under project A — project B's chunks at the same
        ``source_path`` must survive.

        Pre-fix, the in-memory ``_source_index`` keyed on bare
        ``source_path`` and a literal call walked every project's
        chunks at that path. This test pins the L1-A1 isolation
        contract through the deletion path.
        """
        marker = uuid.uuid4().hex[:8]
        # Same source_path under both projects, distinct content.
        source_path = f"iso://delete/{marker}.md"
        content_a = (
            "# Project A Sentinel\n\n"
            f"MET-401 delete-isolation A {marker}: project A's content "
            "uses zinc-plated steel fasteners with M5 thread.\n"
        )
        content_b = (
            "# Project B Sentinel\n\n"
            f"MET-401 delete-isolation B {marker}: project B's content "
            "uses anodized aluminium fasteners with M4 thread.\n"
        )

        await service.ingest(
            content=content_a,
            source_path=source_path,
            knowledge_type=KnowledgeType.DESIGN_DECISION,
            project_id=PROJECT_A,
        )
        await service.ingest(
            content=content_b,
            source_path=source_path,
            knowledge_type=KnowledgeType.DESIGN_DECISION,
            project_id=PROJECT_B,
        )
        time.sleep(0.5)

        # Sanity: both projects can find their respective content under
        # the shared source_path before the delete.
        pre_a = await service.search("zinc-plated steel fasteners", top_k=10, project_id=PROJECT_A)
        pre_b = await service.search("anodized aluminium fasteners", top_k=10, project_id=PROJECT_B)
        assert any(h.source_path == source_path for h in pre_a), (
            "PROJECT_A pre-delete sanity: expected at least one A hit at the source"
        )
        assert any(h.source_path == source_path for h in pre_b), (
            "PROJECT_B pre-delete sanity: expected at least one B hit at the source"
        )

        # Delete only under PROJECT_A. PROJECT_B's chunks must remain.
        await service.delete_by_source(source_path, project_id=PROJECT_A)
        time.sleep(0.5)

        # After delete: A sees nothing at this source; B still sees its
        # content. The L1-A1 contract holds across the deletion path.
        post_a = await service.search("zinc-plated steel fasteners", top_k=10, project_id=PROJECT_A)
        assert not any(h.source_path == source_path for h in post_a), (
            "PROJECT_A's chunks still surface after delete_by_source(project_id=A)"
        )
        post_b = await service.search(
            "anodized aluminium fasteners", top_k=10, project_id=PROJECT_B
        )
        assert any(h.source_path == source_path for h in post_b), (
            "PROJECT_B's chunks at the shared source_path were evicted by "
            "delete_by_source(project_id=A) — isolation contract violated (MET-401)"
        )

    async def test_supersede_respects_project_scope(
        self, service: LightRAGKnowledgeService
    ) -> None:
        """The MET-307 supersede branch must scope its hash lookup and
        eviction to ``(source_path, project_id)``.

        Pre-fix, project B re-ingesting at a path already used by
        project A hashed against A's chunks, mismatched, then evicted
        A's chunks via the unscoped delete_by_source. This test pins
        that scenario as a contract violation: A's content must still
        be searchable under PROJECT_A after B ingests at the same
        source_path with different content.
        """
        marker = uuid.uuid4().hex[:8]
        source_path = f"iso://supersede/{marker}.md"
        # Distinct content per project so the supersede hash check fires.
        content_alpha = (
            "# Alpha\n\n"
            f"MET-401 supersede-iso α {marker}: project A documents the "
            "honeycomb-aluminum structural panel selection.\n"
        )
        content_beta = (
            "# Beta\n\n"
            f"MET-401 supersede-iso β {marker}: project B documents the "
            "carbon-fibre sandwich panel selection.\n"
        )

        # 1. A ingests α under shared source_path.
        await service.ingest(
            content=content_alpha,
            source_path=source_path,
            knowledge_type=KnowledgeType.DESIGN_DECISION,
            project_id=PROJECT_A,
        )
        time.sleep(0.5)

        # 2. B ingests β under the same source_path, different content,
        #    different project. With the fix this must NOT trigger a
        #    cross-project supersede that evicts A's chunks.
        await service.ingest(
            content=content_beta,
            source_path=source_path,
            knowledge_type=KnowledgeType.DESIGN_DECISION,
            project_id=PROJECT_B,
        )
        time.sleep(0.5)

        # A's α phrase must still surface under PROJECT_A.
        hits_a = await service.search(
            "honeycomb-aluminum structural panel", top_k=10, project_id=PROJECT_A
        )
        assert any(h.source_path == source_path for h in hits_a), (
            "PROJECT_A's α content was evicted by PROJECT_B's ingest at the "
            "same source_path — supersede leaked across projects (MET-401)"
        )

        # B's β phrase must surface under PROJECT_B.
        hits_b = await service.search("carbon-fibre sandwich panel", top_k=10, project_id=PROJECT_B)
        assert any(h.source_path == source_path for h in hits_b), (
            "PROJECT_B's β content is not searchable after ingest — "
            "supersede branch broke the project-B write path"
        )

    async def test_unscoped_search_falls_back_to_default_tenant(
        self, service: LightRAGKnowledgeService
    ) -> None:
        """When ``project_id is None`` the service must NOT silently search
        across all projects — it must scope to the documented "default"
        tenant. A document ingested under a real project_id should
        therefore be invisible to an unscoped search.
        """
        sentinel_a = (
            "# Unscoped Default Sentinel\n\n"
            f"MET-401 default-tenant marker {uuid.uuid4().hex[:8]}: "
            "CFRP composite layup at 0/45/90/-45 degrees.\n"
        )
        source_a = f"iso://default-test/{uuid.uuid4().hex[:8]}.md"

        await service.ingest(
            content=sentinel_a,
            source_path=source_a,
            knowledge_type=KnowledgeType.DESIGN_DECISION,
            project_id=PROJECT_A,
        )
        time.sleep(0.5)

        unscoped = await service.search("CFRP composite layup", top_k=10, project_id=None)
        # PROJECT_A's chunk must not appear under the unscoped/default
        # search — that's the whole point of the safer default.
        assert not any(h.source_path == source_a for h in unscoped), (
            "PROJECT_A document appeared under an unscoped search — "
            "default-tenant fallback failed (MET-401)"
        )
