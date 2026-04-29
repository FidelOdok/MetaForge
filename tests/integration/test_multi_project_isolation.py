"""Integration test — knowledge.search respects ctx.project_id (MET-401).

Validates the contract MET-387 set up: when the MCP harness installs
a project_id (via env var or HTTP header), every ingest stamps the
project on the chunk and every subsequent search scopes by it. Two
projects' data co-resident in the same pgvector store don't leak
across the boundary.

Opt in with ``pytest --integration``. Hits real Postgres + LightRAG.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from uuid import UUID

import pytest

from digital_twin.knowledge import create_knowledge_service
from digital_twin.knowledge.types import KnowledgeType
from mcp_core.context import McpCallContext, with_context

pytestmark = pytest.mark.integration


_DEFAULT_DSN = "postgresql://metaforge:metaforge@localhost:5432/metaforge"


def _dsn() -> str:
    return os.environ.get("LIGHTRAG_TEST_DSN", _DEFAULT_DSN)


@pytest.fixture
async def service(tmp_path) -> AsyncIterator:
    """Boot a LightRAGKnowledgeService in a unique pgvector workspace
    so the test doesn't collide with other suites or prior runs.
    """
    suffix = uuid.uuid4().hex[:8]
    svc = create_knowledge_service(
        "lightrag",
        working_dir=str(tmp_path / f"lightrag-met401-{suffix}"),
        postgres_dsn=_dsn(),
        namespace_prefix=f"lightrag_met401_{suffix}",
    )
    await svc.initialize()
    try:
        yield svc
    finally:
        await svc.close()


# A and B are deterministic UUIDs so failure traces are stable.
PROJECT_A = UUID("11111111-aaaa-1111-aaaa-111111111111")
PROJECT_B = UUID("22222222-bbbb-2222-bbbb-222222222222")


SENTINEL_A = "project_a_sentinel_marker"
SENTINEL_B = "project_b_sentinel_marker"


async def _ingest_under(svc, project_id: UUID, source_path: str, content: str) -> None:
    """Helper: ingest under a given project context."""
    ctx = McpCallContext(project_id=project_id, actor_id=f"test:project_{project_id}")
    with with_context(ctx):
        await svc.ingest(
            content=content,
            source_path=source_path,
            knowledge_type=KnowledgeType.DESIGN_DECISION,
        )


class TestMultiProjectIsolation:
    async def test_search_isolation(self, service) -> None:
        """Project A's search must not surface project B's chunks."""
        await _ingest_under(
            service,
            PROJECT_A,
            "uat://met401/decision_a.md",
            f"Project A decision: bracket SR-7 in titanium. {SENTINEL_A}",
        )
        await _ingest_under(
            service,
            PROJECT_B,
            "uat://met401/decision_b.md",
            f"Project B decision: enclosure ABS plastic. {SENTINEL_B}",
        )

        # Search as project A — must find A only.
        ctx_a = McpCallContext(project_id=PROJECT_A, actor_id="test:reader_a")
        with with_context(ctx_a):
            hits_a = await service.search(query="sentinel marker", top_k=10)

        sentinels_a = " ".join(h.content for h in hits_a)
        assert SENTINEL_A in sentinels_a, (
            f"project A search missing its own sentinel. hits={[h.content[:60] for h in hits_a]}"
        )
        assert SENTINEL_B not in sentinels_a, (
            f"project A search leaked project B sentinel. hits={[h.content[:60] for h in hits_a]}"
        )

        # And vice versa.
        ctx_b = McpCallContext(project_id=PROJECT_B, actor_id="test:reader_b")
        with with_context(ctx_b):
            hits_b = await service.search(query="sentinel marker", top_k=10)

        sentinels_b = " ".join(h.content for h in hits_b)
        assert SENTINEL_B in sentinels_b
        assert SENTINEL_A not in sentinels_b

    async def test_explicit_filter_overrides_context(self, service) -> None:
        """Passing ``filters={'project_id': X}`` explicitly wins over
        the ambient context. Provides the cross-project admin escape
        hatch the audit calls for.
        """
        await _ingest_under(
            service,
            PROJECT_A,
            "uat://met401/admin_a.md",
            f"Admin sees A. {SENTINEL_A}",
        )

        # Read AS project B but explicitly ask for A's data.
        ctx_b = McpCallContext(project_id=PROJECT_B, actor_id="user:admin")
        with with_context(ctx_b):
            hits = await service.search(
                query="admin sees",
                top_k=5,
                filters={"project_id": str(PROJECT_A)},
            )
        assert any(SENTINEL_A in h.content for h in hits), [h.content[:60] for h in hits]

    async def test_null_project_returns_unscoped(self, service) -> None:
        """``ctx.project_id = None`` returns chunks from every project.

        **Pinned behaviour**: null context = admin/global view, not
        lock-down. This is the safer default for backward-compat with
        legacy callers (no MCP harness in front), and the explicit
        ``project_id`` filter still works as the lock-down path. If
        a future deployment needs a stricter default, that's a config
        toggle, not the default behaviour.
        """
        await _ingest_under(
            service,
            PROJECT_A,
            "uat://met401/null_a.md",
            f"Null context A view. {SENTINEL_A}",
        )
        await _ingest_under(
            service,
            PROJECT_B,
            "uat://met401/null_b.md",
            f"Null context B view. {SENTINEL_B}",
        )

        # No context installed — the default sentinel context has
        # project_id=None.
        hits = await service.search(query="null context", top_k=10)
        contents = " ".join(h.content for h in hits)
        assert SENTINEL_A in contents
        assert SENTINEL_B in contents

    async def test_ingest_explicit_metadata_overrides_context(self, service) -> None:
        """Passing ``metadata={'project_id': X}`` to ingest wins over
        the ambient context. Consistent with the search-side override.
        """
        # Ingest AS B but with explicit metadata = A. The chunk should
        # show up in A's search, not B's.
        ctx_b = McpCallContext(project_id=PROJECT_B, actor_id="user:admin")
        with with_context(ctx_b):
            await service.ingest(
                content=f"Cross-project admin ingest. {SENTINEL_A}",
                source_path="uat://met401/cross_admin.md",
                knowledge_type=KnowledgeType.DESIGN_DECISION,
                metadata={"project_id": str(PROJECT_A)},
            )

        ctx_a = McpCallContext(project_id=PROJECT_A, actor_id="test:reader_a")
        with with_context(ctx_a):
            hits = await service.search(query="cross-project admin", top_k=5)
        assert any(SENTINEL_A in h.content for h in hits), [h.content[:60] for h in hits]
