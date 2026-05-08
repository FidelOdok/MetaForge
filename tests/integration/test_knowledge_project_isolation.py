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
PROJECT_C = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")


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

    async def test_isolation_holds_across_three_projects(
        self, service: LightRAGKnowledgeService
    ) -> None:
        """Three-way isolation: A, B, C each ingest a distinctive document
        under the same query terms; each project's search must return only
        its own content, never another project's. Hardens the L1-A1 A↔B
        contract by extending it to a third tenant — any leak in any
        direction (A→B, A→C, B→A, B→C, C→A, C→B) fails the test.
        """
        marker = uuid.uuid4().hex[:8]

        content_a = (
            f"# Three-Way Isolation Sentinel A {marker}\n\n"
            "MET-401 three-way marker A: titanium grade 5 fastener spec "
            "in shared aerospace bracket family.\n"
        )
        content_b = (
            f"# Three-Way Isolation Sentinel B {marker}\n\n"
            "MET-401 three-way marker B: aluminium 7075-T6 fastener spec "
            "in shared aerospace bracket family.\n"
        )
        content_c = (
            f"# Three-Way Isolation Sentinel C {marker}\n\n"
            "MET-401 three-way marker C: stainless 316 fastener spec "
            "in shared aerospace bracket family.\n"
        )
        source_a = f"iso://3way/{marker}-a.md"
        source_b = f"iso://3way/{marker}-b.md"
        source_c = f"iso://3way/{marker}-c.md"

        await service.ingest(
            content=content_a,
            source_path=source_a,
            knowledge_type=KnowledgeType.DESIGN_DECISION,
            project_id=PROJECT_A,
        )
        await service.ingest(
            content=content_b,
            source_path=source_b,
            knowledge_type=KnowledgeType.DESIGN_DECISION,
            project_id=PROJECT_B,
        )
        await service.ingest(
            content=content_c,
            source_path=source_c,
            knowledge_type=KnowledgeType.DESIGN_DECISION,
            project_id=PROJECT_C,
        )
        time.sleep(0.5)

        query = "fastener spec in shared aerospace bracket family"

        hits_a = await service.search(query, top_k=10, project_id=PROJECT_A)
        hits_b = await service.search(query, top_k=10, project_id=PROJECT_B)
        hits_c = await service.search(query, top_k=10, project_id=PROJECT_C)

        # Each project sees its own source_path only — no leaks in any
        # direction. We assert against the relevant source set so an
        # unrelated test's chunks (different namespace_prefix per fixture)
        # can't pollute the result.
        sources_a = {h.source_path for h in hits_a}
        sources_b = {h.source_path for h in hits_b}
        sources_c = {h.source_path for h in hits_c}

        assert source_a in sources_a, "PROJECT_A search missed its own document"
        assert source_b in sources_b, "PROJECT_B search missed its own document"
        assert source_c in sources_c, "PROJECT_C search missed its own document"

        # No cross-tenant leakage in any direction.
        assert source_b not in sources_a and source_c not in sources_a, (
            "PROJECT_A search leaked B/C documents — three-way isolation broken"
        )
        assert source_a not in sources_b and source_c not in sources_b, (
            "PROJECT_B search leaked A/C documents — three-way isolation broken"
        )
        assert source_a not in sources_c and source_b not in sources_c, (
            "PROJECT_C search leaked A/B documents — three-way isolation broken"
        )

        # Stronger metadata stamp check: every hit must carry the
        # current project's project_id stamp.
        assert all(h.metadata.get("project_id") == str(PROJECT_A) for h in hits_a), (
            "PROJECT_A hits carried a non-A project_id stamp"
        )
        assert all(h.metadata.get("project_id") == str(PROJECT_B) for h in hits_b), (
            "PROJECT_B hits carried a non-B project_id stamp"
        )
        assert all(h.metadata.get("project_id") == str(PROJECT_C) for h in hits_c), (
            "PROJECT_C hits carried a non-C project_id stamp"
        )

    async def test_list_sources_respects_project_scope(
        self, service: LightRAGKnowledgeService
    ) -> None:
        """``list_sources(project_id=...)`` (L1-A8) honours the project
        scope: ingesting 2 sources under A and 1 under B yields 2 rows
        for A, 1 row for B, and the unscoped (``None``) call returns the
        documented default-tenant scope (per L1-A1: "default tenant only",
        so 0 rows here since neither A nor B is the default tenant).
        """
        marker = uuid.uuid4().hex[:8]
        source_a1 = f"iso://list-sources/{marker}-a1.md"
        source_a2 = f"iso://list-sources/{marker}-a2.md"
        source_b1 = f"iso://list-sources/{marker}-b1.md"

        await service.ingest(
            content=f"# A1 {marker}\n\nProject A first source content.\n",
            source_path=source_a1,
            knowledge_type=KnowledgeType.DESIGN_DECISION,
            project_id=PROJECT_A,
        )
        await service.ingest(
            content=f"# A2 {marker}\n\nProject A second source content.\n",
            source_path=source_a2,
            knowledge_type=KnowledgeType.DESIGN_DECISION,
            project_id=PROJECT_A,
        )
        await service.ingest(
            content=f"# B1 {marker}\n\nProject B only source content.\n",
            source_path=source_b1,
            knowledge_type=KnowledgeType.DESIGN_DECISION,
            project_id=PROJECT_B,
        )
        time.sleep(0.5)

        # Filter to the sources we just ingested so unrelated rows in
        # the workspace (other tests, prior runs reusing PG) can't make
        # the count assertions flaky. The test's contract is that A's
        # listing contains exactly our two A sources and never the B
        # source, and vice versa.
        our_sources = {source_a1, source_a2, source_b1}

        rows_a = await service.list_sources(project_id=PROJECT_A)
        a_paths = {r.source_path for r in rows_a if r.source_path in our_sources}
        assert a_paths == {source_a1, source_a2}, (
            f"PROJECT_A list_sources expected {{source_a1, source_a2}}, "
            f"got {a_paths} (B leak: {source_b1 in a_paths})"
        )

        rows_b = await service.list_sources(project_id=PROJECT_B)
        b_paths = {r.source_path for r in rows_b if r.source_path in our_sources}
        assert b_paths == {source_b1}, (
            f"PROJECT_B list_sources expected {{source_b1}}, "
            f"got {b_paths} (A leak: any of A's sources in {b_paths})"
        )

        # Unscoped (project_id=None) -> default-tenant scope. Neither A
        # nor B is the default tenant, so none of our just-ingested
        # sources should appear.
        rows_default = await service.list_sources(project_id=None)
        default_paths = {r.source_path for r in rows_default if r.source_path in our_sources}
        assert default_paths == set(), (
            "Unscoped list_sources returned project A or B sources — "
            "default-tenant scope leaked into None case (MET-401)"
        )
