"""Unit tests for the knowledge MCP tool adapter (MET-335).

Verifies:

* The adapter registers ``knowledge.search`` and ``knowledge.ingest``.
* The handlers delegate 1:1 to the underlying ``KnowledgeService`` and
  produce JSON-serialisable output (UUIDs / enums coerced to strings).
* The adapter never imports a concrete backend (no LightRAG / LlamaIndex
  symbols leak in).
* ``set_service`` late-binds a service constructed without one.
"""

from __future__ import annotations

import importlib
import inspect
from typing import Any
from uuid import UUID, uuid4

import pytest

from digital_twin.knowledge.service import IngestResult, KnowledgeService, SearchHit
from digital_twin.knowledge.types import KnowledgeType
from tool_registry.tools.knowledge.adapter import KnowledgeServer


class _FakeService:
    """Records calls so the test can assert exact delegation."""

    def __init__(self) -> None:
        self.search_calls: list[dict[str, Any]] = []
        self.ingest_calls: list[dict[str, Any]] = []

    async def ingest(
        self,
        content: str,
        source_path: str,
        knowledge_type: KnowledgeType,
        source_work_product_id: UUID | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> IngestResult:
        self.ingest_calls.append(
            {
                "content": content,
                "source_path": source_path,
                "knowledge_type": knowledge_type,
                "source_work_product_id": source_work_product_id,
                "metadata": metadata,
            }
        )
        return IngestResult(
            entry_ids=[uuid4(), uuid4()],
            chunks_indexed=2,
            source_path=source_path,
        )

    async def search(
        self,
        query: str,
        top_k: int = 5,
        knowledge_type: KnowledgeType | None = None,
        filters: dict[str, Any] | None = None,
    ) -> list[SearchHit]:
        self.search_calls.append(
            {
                "query": query,
                "top_k": top_k,
                "knowledge_type": knowledge_type,
                "filters": filters,
            }
        )
        return [
            SearchHit(
                content=f"hit for {query}",
                similarity_score=0.91,
                source_path="docs/decision.md",
                heading="Decision",
                chunk_index=2,
                total_chunks=5,
                metadata={"author": "mech"},
                knowledge_type=KnowledgeType.DESIGN_DECISION,
                source_work_product_id=uuid4(),
            ),
        ]

    async def delete_by_source(self, source_path: str) -> int:
        return 0

    async def health_check(self) -> dict[str, Any]:
        return {"status": "ok", "backend": "fake"}


@pytest.fixture
def server() -> KnowledgeServer:
    return KnowledgeServer(service=_FakeService())  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_registers_two_tools(self, server: KnowledgeServer) -> None:
        assert set(server.tool_ids) == {"knowledge.search", "knowledge.ingest"}

    def test_search_manifest_shape(self, server: KnowledgeServer) -> None:
        registration = server._tools["knowledge.search"]  # noqa: SLF001
        manifest = registration.manifest
        assert manifest.adapter_id == "knowledge"
        assert "query" in manifest.input_schema["properties"]
        assert "top_k" in manifest.input_schema["properties"]
        assert manifest.input_schema["required"] == ["query"]

    def test_ingest_manifest_shape(self, server: KnowledgeServer) -> None:
        registration = server._tools["knowledge.ingest"]  # noqa: SLF001
        manifest = registration.manifest
        assert manifest.adapter_id == "knowledge"
        required = manifest.input_schema["required"]
        assert set(required) == {"content", "source_path", "knowledge_type"}


# ---------------------------------------------------------------------------
# Handler delegation
# ---------------------------------------------------------------------------


class TestSearchHandler:
    async def test_delegates_query_top_k_and_returns_serialised_hits(
        self, server: KnowledgeServer
    ) -> None:
        result = await server.handle_search(
            {"query": "titanium bracket", "top_k": 3, "knowledge_type": "design_decision"}
        )
        # Delegation
        service = server.service  # type: ignore[attr-defined]
        assert len(service.search_calls) == 1  # type: ignore[attr-defined]
        call = service.search_calls[0]  # type: ignore[attr-defined]
        assert call["query"] == "titanium bracket"
        assert call["top_k"] == 3
        assert call["knowledge_type"] == KnowledgeType.DESIGN_DECISION

        # Serialisation — UUID / enum should be strings
        assert result["hits"]
        hit = result["hits"][0]
        assert hit["similarity_score"] == 0.91
        assert hit["heading"] == "Decision"
        assert isinstance(hit["source_work_product_id"], str)
        assert hit["knowledge_type"] == "design_decision"

    async def test_missing_query_raises(self, server: KnowledgeServer) -> None:
        with pytest.raises(ValueError, match="query"):
            await server.handle_search({})


class TestIngestHandler:
    async def test_delegates_and_serialises_uuid_list(self, server: KnowledgeServer) -> None:
        wp = uuid4()
        result = await server.handle_ingest(
            {
                "content": "Decision body",
                "source_path": "/tmp/d.md",
                "knowledge_type": "design_decision",
                "source_work_product_id": str(wp),
                "metadata": {"reviewer": "mech"},
            }
        )
        service = server.service  # type: ignore[attr-defined]
        assert len(service.ingest_calls) == 1  # type: ignore[attr-defined]
        call = service.ingest_calls[0]  # type: ignore[attr-defined]
        assert call["source_path"] == "/tmp/d.md"
        assert call["source_work_product_id"] == wp
        assert call["knowledge_type"] == KnowledgeType.DESIGN_DECISION

        assert result["chunks_indexed"] == 2
        assert result["source_path"] == "/tmp/d.md"
        assert len(result["entry_ids"]) == 2
        assert all(isinstance(eid, str) for eid in result["entry_ids"])

    async def test_missing_required_fields_raise(self, server: KnowledgeServer) -> None:
        with pytest.raises(ValueError, match="content"):
            await server.handle_ingest({"source_path": "x", "knowledge_type": "session"})
        with pytest.raises(ValueError, match="source_path"):
            await server.handle_ingest({"content": "x", "knowledge_type": "session"})
        with pytest.raises(ValueError, match="knowledge_type"):
            await server.handle_ingest(
                {"content": "x", "source_path": "y", "knowledge_type": "not-a-type"}
            )


# ---------------------------------------------------------------------------
# Late binding
# ---------------------------------------------------------------------------


class TestLateBinding:
    def test_construct_without_service_then_bind(self) -> None:
        server = KnowledgeServer()
        with pytest.raises(RuntimeError, match="set_service"):
            _ = server.service
        server.set_service(_FakeService())  # type: ignore[arg-type]
        assert server.service is not None


# ---------------------------------------------------------------------------
# Independence from any concrete backend
# ---------------------------------------------------------------------------


class TestProviderIndependence:
    def test_adapter_module_imports_no_concrete_backend(self) -> None:
        """The adapter must depend only on the ``KnowledgeService`` Protocol.

        Walks the source AST of ``tool_registry.tools.knowledge.adapter``
        and asserts that no LightRAG / LlamaIndex symbols are imported.
        """
        module = importlib.import_module("tool_registry.tools.knowledge.adapter")
        source = inspect.getsource(module)
        forbidden = ["lightrag", "llama_index", "lightrag_service", "LightRAGKnowledge"]
        offenders = [needle for needle in forbidden if needle in source]
        assert not offenders, f"adapter leaks concrete backend imports: {offenders}"

    def test_satisfies_runtime_checkable_service(self, server: KnowledgeServer) -> None:
        """The fake injected into the server still passes the Protocol check."""
        assert isinstance(server.service, KnowledgeService)
