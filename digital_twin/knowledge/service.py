"""Public ``KnowledgeService`` interface for the L1 knowledge layer.

Defines the framework-agnostic contract that all knowledge backends
(LightRAG, LlamaIndex, custom) must satisfy. Per ADR-008, the interface
is the swap-out clause: callers depend on this Protocol, never on the
concrete implementation.

The dataclasses (``IngestResult``, ``SearchHit``, ``SourceSummary``) are
deliberately plain ``@dataclass`` rather than Pydantic models so the
public contract stays free of any Pydantic-version coupling and is
trivially constructible from any backend.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from digital_twin.knowledge.types import KnowledgeType

__all__ = [
    "IngestResult",
    "KnowledgeService",
    "SearchHit",
    "SourceSummary",
]


@dataclass
class IngestResult:
    """Outcome of a single ``ingest()`` call.

    ``entry_ids`` is one UUID per chunk written to the backing store —
    callers that only care about throughput can read ``chunks_indexed``.
    """

    entry_ids: list[UUID]
    chunks_indexed: int
    source_path: str


@dataclass
class SearchHit:
    """One result from a ``search()`` call.

    Citation fields (``source_path``, ``heading``, ``chunk_index``,
    ``total_chunks``) round-trip through ingest -> store -> search so
    downstream UI / RAG prompts can render an attributable answer.
    """

    content: str
    similarity_score: float
    source_path: str | None
    heading: str | None
    chunk_index: int | None
    total_chunks: int | None
    metadata: dict[str, Any] = field(default_factory=dict)
    knowledge_type: KnowledgeType | None = None
    source_work_product_id: UUID | None = None


@dataclass
class SourceSummary:
    """Aggregated row returned by ``list_sources()``.

    One ``SourceSummary`` per ``(source_path, knowledge_type)`` pair —
    chunks of a multi-chunk source roll up into a single row whose
    ``fragment_count`` reports the chunk total. ``indexed_at`` is the
    most-recent ``created_at`` across all chunks of the source so the
    default ordering (newest first) reflects the last edit, not the
    initial ingest.

    Unblocks the ``metaforge://knowledge/sources`` MCP resource (L1-B1)
    and the ``forge sources list/show/delete`` CLI (L1-C1) — both call
    ``list_sources`` and project this dataclass directly into their
    output shape, which is why it lives in ``service.py`` (the public
    contract module) rather than the LightRAG adapter.
    """

    source_path: str
    knowledge_type: KnowledgeType | str | None
    fragment_count: int
    indexed_at: datetime
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class KnowledgeService(Protocol):
    """The L1 knowledge contract.

    Implementations:
      * ``LightRAGKnowledgeService`` — production default (ADR-008).
      * future ``LlamaIndexKnowledgeService`` — fallback if LightRAG
        decays the way R2R did.

    All methods are async so the wire-protocol layer can suspend during
    embedding / vector-search round-trips without blocking the gateway
    event loop. Sync backends must wrap their calls in
    ``asyncio.to_thread()`` inside the adapter.
    """

    async def ingest(
        self,
        content: str,
        source_path: str,
        knowledge_type: KnowledgeType,
        source_work_product_id: UUID | None = None,
        metadata: dict[str, Any] | None = None,
        project_id: UUID | None = None,
        actor_id: str | None = None,
    ) -> IngestResult: ...

    async def search(
        self,
        query: str,
        top_k: int = 5,
        knowledge_type: KnowledgeType | None = None,
        filters: dict[str, Any] | None = None,
        project_id: UUID | None = None,
        rerank: bool = False,
        actor_id: str | None = None,
    ) -> list[SearchHit]: ...

    async def delete_by_source(self, source_path: str) -> int: ...

    async def list_sources(
        self,
        project_id: UUID | None = None,
        knowledge_type: KnowledgeType | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SourceSummary]: ...

    async def health_check(self) -> dict[str, Any]: ...
