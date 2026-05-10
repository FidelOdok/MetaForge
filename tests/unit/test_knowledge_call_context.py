"""Per-call context propagation in the knowledge MCP adapter (MET-387).

Verifies the MET-387 contract: the knowledge adapter handlers must read
``current_context().project_id`` AND ``actor_id`` and forward both
through ``KnowledgeService.{ingest,search}`` calls **and** stamp them on
the OTel span as ``mcp.project_id`` / ``mcp.actor_id``.

L1-A1 already shipped project_id forwarding; these tests add actor_id
coverage and pin the OTel attribute names so downstream observability
(Loki dashboards, Tempo traces) can rely on them without drift.

The fake service mirrors the ``_FakeService`` pattern in
``tests/unit/test_knowledge_mcp_adapter.py`` but records ``actor_id``.
The OTel harness mirrors ``tests/unit/test_mcp_otel_instrumentation.py``
— a session-singleton ``TracerProvider`` with an ``InMemorySpanExporter``
attached as a SimpleSpanProcessor.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from uuid import UUID, uuid4

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from digital_twin.knowledge.service import IngestResult, SearchHit, SourceSummary
from digital_twin.knowledge.types import KnowledgeType
from mcp_core.context import McpCallContext, with_context
from tool_registry.tools.knowledge.adapter import KnowledgeServer

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _FakeService:
    """Records the kwargs passed to ``ingest`` / ``search``.

    Same shape as the fake in ``test_knowledge_mcp_adapter.py``; we
    duplicate it here so the call-context tests stay self-contained
    and so the assertions on ``actor_id`` are co-located with the
    behaviour they pin.
    """

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
        project_id: UUID | None = None,
        actor_id: str | None = None,
    ) -> IngestResult:
        # Mirror the production behaviour of stamping actor_id into
        # chunk metadata so the metadata-carry test can assert against
        # the resulting dict shape.
        merged_metadata: dict[str, Any] = dict(metadata or {})
        if actor_id is not None:
            merged_metadata["actor_id"] = actor_id
        self.ingest_calls.append(
            {
                "content": content,
                "source_path": source_path,
                "knowledge_type": knowledge_type,
                "source_work_product_id": source_work_product_id,
                "metadata": merged_metadata,
                "project_id": project_id,
                "actor_id": actor_id,
            }
        )
        return IngestResult(
            entry_ids=[uuid4()],
            chunks_indexed=1,
            source_path=source_path,
        )

    async def search(
        self,
        query: str,
        top_k: int = 5,
        knowledge_type: KnowledgeType | None = None,
        filters: dict[str, Any] | None = None,
        project_id: UUID | None = None,
        actor_id: str | None = None,
    ) -> list[SearchHit]:
        self.search_calls.append(
            {
                "query": query,
                "top_k": top_k,
                "knowledge_type": knowledge_type,
                "filters": filters,
                "project_id": project_id,
                "actor_id": actor_id,
            }
        )
        return []

    async def delete_by_source(
        self, source_path: str, project_id: UUID | None = None
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

    async def health_check(self) -> dict[str, Any]:
        return {"status": "ok", "backend": "fake"}


@pytest.fixture
def server() -> KnowledgeServer:
    return KnowledgeServer(service=_FakeService())  # type: ignore[arg-type]


@pytest.fixture
def span_exporter() -> Iterator[InMemorySpanExporter]:
    """Attach an in-memory span exporter to the active TracerProvider.

    Same pattern as ``tests/unit/test_mcp_otel_instrumentation.py`` —
    OTel forbids replacing the global TracerProvider after first use,
    so we install a fresh SDK provider only if the active one is the
    NoOp default. SimpleSpanProcessor flushes synchronously so no
    explicit shutdown is needed before assertions.
    """
    exporter = InMemorySpanExporter()
    provider = trace.get_tracer_provider()
    if not hasattr(provider, "add_span_processor"):
        provider = TracerProvider()
        trace.set_tracer_provider(provider)
    processor = SimpleSpanProcessor(exporter)
    provider.add_span_processor(processor)  # type: ignore[attr-defined]
    try:
        yield exporter
    finally:
        exporter.clear()


# ---------------------------------------------------------------------------
# Forwarding through the service Protocol
# ---------------------------------------------------------------------------


_PROJECT = UUID("33333333-3333-4333-8333-333333333333")
_ACTOR = "claude-uat-runner"


class TestActorIdForwarding:
    """Adapter handlers must forward ``current_context().actor_id``."""

    async def test_search_forwards_actor_id_from_context(self, server: KnowledgeServer) -> None:
        with with_context(McpCallContext(project_id=_PROJECT, actor_id=_ACTOR)):
            await server.handle_search({"query": "anything"})

        service = server.service  # type: ignore[attr-defined]
        assert service.search_calls[-1]["actor_id"] == _ACTOR  # type: ignore[attr-defined]

    async def test_ingest_forwards_actor_id_from_context(self, server: KnowledgeServer) -> None:
        with with_context(McpCallContext(project_id=_PROJECT, actor_id=_ACTOR)):
            await server.handle_ingest(
                {
                    "content": "body",
                    "source_path": "/x.md",
                    "knowledge_type": "session",
                }
            )

        service = server.service  # type: ignore[attr-defined]
        assert service.ingest_calls[-1]["actor_id"] == _ACTOR  # type: ignore[attr-defined]

    async def test_no_context_uses_none_actor_id(self, server: KnowledgeServer) -> None:
        # Default sentinel context has actor_id="system:unattributed".
        # The adapter's ``_resolve_actor_id`` projects that sentinel to
        # ``None`` so the service / span / log layer can elide the
        # field rather than stamping a misleading default onto every
        # chunk's metadata.
        await server.handle_search({"query": "anything"})
        await server.handle_ingest(
            {
                "content": "body",
                "source_path": "/x.md",
                "knowledge_type": "session",
            }
        )

        service = server.service  # type: ignore[attr-defined]
        assert service.search_calls[-1]["actor_id"] is None  # type: ignore[attr-defined]
        assert service.ingest_calls[-1]["actor_id"] is None  # type: ignore[attr-defined]


class TestActorIdMetadata:
    async def test_ingest_metadata_carries_actor_id(self, server: KnowledgeServer) -> None:
        with with_context(McpCallContext(project_id=_PROJECT, actor_id=_ACTOR)):
            await server.handle_ingest(
                {
                    "content": "body",
                    "source_path": "/x.md",
                    "knowledge_type": "session",
                }
            )

        # The fake mirrors the production behaviour of stamping
        # actor_id into chunk metadata. The L1 contract is: when
        # actor_id is forwarded, it lands in metadata so search hits
        # surface it for attribution.
        service = server.service  # type: ignore[attr-defined]
        recorded = service.ingest_calls[-1]["metadata"]  # type: ignore[attr-defined]
        assert recorded["actor_id"] == _ACTOR


# ---------------------------------------------------------------------------
# OTel span attributes
# ---------------------------------------------------------------------------


def _adapter_span(exporter: InMemorySpanExporter, name: str) -> Any:
    spans = [s for s in exporter.get_finished_spans() if s.name == name]
    assert spans, f"expected a {name!r} span; got {[s.name for s in exporter.get_finished_spans()]}"
    return spans[-1]


class TestOtelSpanAttributes:
    """Both ingest + search spans must carry ``mcp.project_id`` / ``mcp.actor_id``."""

    async def test_search_sets_otel_span_attributes(
        self,
        server: KnowledgeServer,
        span_exporter: InMemorySpanExporter,
    ) -> None:
        with with_context(McpCallContext(project_id=_PROJECT, actor_id=_ACTOR)):
            await server.handle_search({"query": "anything"})

        span = _adapter_span(span_exporter, "knowledge.mcp.search")
        attrs = dict(span.attributes or {})
        assert attrs.get("mcp.project_id") == str(_PROJECT)
        assert attrs.get("mcp.actor_id") == _ACTOR

    async def test_ingest_sets_otel_span_attributes(
        self,
        server: KnowledgeServer,
        span_exporter: InMemorySpanExporter,
    ) -> None:
        with with_context(McpCallContext(project_id=_PROJECT, actor_id=_ACTOR)):
            await server.handle_ingest(
                {
                    "content": "body",
                    "source_path": "/x.md",
                    "knowledge_type": "session",
                }
            )

        span = _adapter_span(span_exporter, "knowledge.mcp.ingest")
        attrs = dict(span.attributes or {})
        assert attrs.get("mcp.project_id") == str(_PROJECT)
        assert attrs.get("mcp.actor_id") == _ACTOR

    async def test_no_actor_id_means_attribute_unset(
        self,
        server: KnowledgeServer,
        span_exporter: InMemorySpanExporter,
    ) -> None:
        # Default sentinel context — ``mcp.actor_id`` should NOT be
        # stamped onto the span. Pinning the "unset" branch is the
        # other half of the no-context contract.
        await server.handle_search({"query": "anything"})

        span = _adapter_span(span_exporter, "knowledge.mcp.search")
        attrs = dict(span.attributes or {})
        assert "mcp.actor_id" not in attrs
