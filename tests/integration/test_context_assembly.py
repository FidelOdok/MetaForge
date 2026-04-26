"""Integration test for context assembly (MET-315).

Opt in with ``pytest --integration``. Requires the dev
``metaforge-postgres-1`` (with ``vector`` extension) AND
``metaforge-neo4j-1`` reachable on the local docker-compose ports.

Asserts the end-to-end contract: ingest a markdown doc through a real
``LightRAGKnowledgeService``, create a corresponding work product in a
real ``Neo4jGraphEngine``, then run ``ContextAssembler.assemble`` with
``ContextScope.ALL`` and confirm both fragment kinds appear with valid
attribution.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from digital_twin.context import (
    ContextAssembler,
    ContextAssemblyRequest,
    ContextScope,
    ContextSourceKind,
)
from digital_twin.knowledge import create_knowledge_service
from digital_twin.knowledge.types import KnowledgeType
from twin_core.api import InMemoryTwinAPI
from twin_core.models.enums import WorkProductType
from twin_core.models.work_product import WorkProduct

pytestmark = pytest.mark.integration


_DEFAULT_DSN = "postgresql://metaforge:metaforge@localhost:5432/metaforge"


def _dsn() -> str:
    return os.environ.get("DATABASE_URL", _DEFAULT_DSN).replace(
        "postgresql+asyncpg://", "postgresql://"
    )


@pytest.fixture(autouse=True)
def _neo4j_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEO4J_URI", os.environ.get("NEO4J_URI", "bolt://localhost:7687"))
    monkeypatch.setenv("NEO4J_USER", os.environ.get("NEO4J_USER", "neo4j"))
    monkeypatch.setenv("NEO4J_PASSWORD", os.environ.get("NEO4J_PASSWORD", "metaforge"))


@pytest.fixture
async def twin() -> AsyncIterator[InMemoryTwinAPI]:
    api = await InMemoryTwinAPI.create_from_env()
    try:
        yield api
    finally:
        graph = api._graph  # noqa: SLF001
        if hasattr(graph, "close"):
            await graph.close()


@pytest.fixture
async def knowledge_service(tmp_path: Path) -> AsyncIterator[object]:
    suffix = uuid.uuid4().hex[:8]
    svc = create_knowledge_service(
        "lightrag",
        working_dir=str(tmp_path / f"lightrag-{suffix}"),
        postgres_dsn=_dsn(),
        namespace_prefix=f"lightrag_ctx_{suffix}",
    )
    await svc.initialize()  # type: ignore[attr-defined]
    try:
        yield svc
    finally:
        await svc.close()  # type: ignore[attr-defined]


class TestEndToEnd:
    async def test_assemble_combines_neo4j_and_pgvector(
        self,
        twin: InMemoryTwinAPI,
        knowledge_service: object,
    ) -> None:
        sentinel = uuid.uuid4().hex[:8]
        wp = WorkProduct(
            id=uuid.uuid4(),
            name=f"context-test-{sentinel}",
            type=WorkProductType.CAD_MODEL,
            domain="mechanical",
            file_path=f"cad/context-{sentinel}.step",
            content_hash=sentinel,
            format="step",
            created_by="met-315-test",
        )
        try:
            await twin.create_work_product(wp)

            content = (
                f"# Decision {sentinel}\n\n"
                "We choose titanium grade 5 for the SR-7 mounting bracket. "
                "The aluminium 6061-T6 prototype failed thermal-cycle testing. "
                "Approved by mechanical lead on 2026-04-12.\n"
            )
            await knowledge_service.ingest(  # type: ignore[attr-defined]
                content=content,
                source_path=f"context-test-{sentinel}.md",
                knowledge_type=KnowledgeType.DESIGN_DECISION,
            )

            assembler = ContextAssembler(twin=twin, knowledge_service=knowledge_service)  # type: ignore[arg-type]
            request = ContextAssemblyRequest(
                agent_id="mech_agent",
                query=f"titanium grade 5 SR-7 bracket {sentinel}",
                scope=[ContextScope.ALL],
                work_product_id=wp.id,
                graph_depth=0,
                token_budget=2000,
            )
            response = await assembler.assemble(request)

            kinds = {f.source_kind for f in response.fragments}
            attribution = [(f.source_kind, f.source_id) for f in response.fragments]
            assert ContextSourceKind.KNOWLEDGE_HIT in kinds, (
                f"missing knowledge fragment in {attribution}"
            )
            assert ContextSourceKind.GRAPH_NODE in kinds, "missing graph fragment"

            wp_fragment = next(
                f for f in response.fragments if f.source_id == f"work_product://{wp.id}"
            )
            assert wp_fragment.work_product_id == wp.id

            knowledge_fragment = next(
                f for f in response.fragments if f.source_kind == ContextSourceKind.KNOWLEDGE_HIT
            )
            assert knowledge_fragment.source_path == f"context-test-{sentinel}.md"
            assert knowledge_fragment.similarity_score is not None
            assert knowledge_fragment.similarity_score > 0

            assert response.token_count > 0
            assert response.token_count <= request.token_budget
            assert response.metadata["agent_id"] == "mech_agent"
        finally:
            await twin.delete_work_product(wp.id)
