"""Knowledge API routes for semantic search and ingestion.

Endpoints live under ``/api/v1/knowledge``.

When ``app.state.knowledge_service`` is wired (production gateway —
LightRAG via ``digital_twin.knowledge``), every read and write routes
through the service so dedup, predelete, and citation-field
round-tripping all happen consistently. The legacy
``app.state.knowledge_store`` path is still honoured for unit tests
that haven't migrated yet — see MET-390.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid5

import structlog
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from digital_twin.knowledge.store import KnowledgeType
from observability.tracing import get_tracer

# Namespace UUID for deriving deterministic entry IDs from a
# ``(source_path, chunk_index)`` pair when the underlying
# ``KnowledgeService`` doesn't already expose a UUID per chunk.
_ENTRY_ID_NAMESPACE = UUID("4f3c4f0a-1ae6-4b9c-a4a3-0e2c4d3a1b2f")

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.knowledge")

router = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge"])


# ---------------------------------------------------------------------------
# Response / request models
# ---------------------------------------------------------------------------


class KnowledgeEntryResponse(BaseModel):
    """API response model for a single knowledge entry."""

    id: UUID
    content: str
    knowledge_type: KnowledgeType = Field(alias="knowledgeType")
    metadata: dict[str, Any]
    source_work_product_id: UUID | None = Field(default=None, alias="sourceWorkProductId")
    source_path: str | None = Field(default=None, alias="sourcePath")
    chunk_index: int | None = Field(default=None, alias="chunkIndex")
    total_chunks: int | None = Field(default=None, alias="totalChunks")
    created_at: datetime = Field(alias="createdAt")

    model_config = {"populate_by_name": True}


class SearchResponse(BaseModel):
    """Response from the knowledge search endpoint."""

    results: list[KnowledgeEntryResponse]
    query: str
    total_found: int = Field(alias="totalFound")

    model_config = {"populate_by_name": True}


class IngestRequest(BaseModel):
    """Request body for manual knowledge ingestion."""

    content: str = Field(..., min_length=1)
    knowledge_type: KnowledgeType = Field(alias="knowledgeType")
    metadata: dict[str, Any] = Field(default_factory=dict)
    source_work_product_id: UUID | None = Field(default=None, alias="sourceWorkProductId")
    source_path: str | None = Field(default=None, alias="sourcePath")

    model_config = {"populate_by_name": True}


class IngestResponse(BaseModel):
    """Response from the knowledge ingest endpoint."""

    entry_id: UUID = Field(alias="entryId")
    embedded: bool

    model_config = {"populate_by_name": True}


class IngestDocumentRequest(BaseModel):
    """Request body for L1 document ingestion via ``KnowledgeService`` (MET-336)."""

    content: str = Field(..., min_length=1)
    source_path: str = Field(..., min_length=1, alias="sourcePath")
    knowledge_type: KnowledgeType = Field(alias="knowledgeType")
    source_work_product_id: UUID | None = Field(default=None, alias="sourceWorkProductId")
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}


class IngestDocumentResponse(BaseModel):
    """L1 ingest result — mirrors ``IngestResult`` (MET-336)."""

    entry_ids: list[UUID] = Field(alias="entryIds")
    chunks_indexed: int = Field(alias="chunksIndexed")
    source_path: str = Field(alias="sourcePath")

    model_config = {"populate_by_name": True}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_store(request: Request) -> Any:
    store = getattr(request.app.state, "knowledge_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Knowledge store not initialized")
    return store


def _get_embedding(request: Request) -> Any:
    svc = getattr(request.app.state, "embedding_service", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="Embedding service not initialized")
    return svc


def _maybe_service(request: Request) -> Any | None:
    """Return the active ``KnowledgeService`` if one is wired; else None.

    The production gateway sets ``app.state.knowledge_service`` to a
    LightRAG-backed instance (MET-346). When present, all read/write
    operations on ``/ingest`` and ``/search`` flow through the same
    service so they see the same data — closes the dual-storage gap
    surfaced by Tier-2 (MET-390).

    Unit tests that only initialise ``knowledge_store`` continue to
    work via the legacy path.
    """
    return getattr(request.app.state, "knowledge_service", None)


def _entry_id_for(source_path: str | None, chunk_index: int | None) -> UUID:
    """Deterministic entry-ID for a chunk surfaced via ``KnowledgeService``.

    The service-layer ``SearchHit`` doesn't carry a UUID; we mint one
    from ``source_path + chunk_index`` so the same chunk always maps
    to the same response ``id`` across calls — which keeps consumers
    that key on ``id`` (dashboard, MCP search bridge) stable.
    """
    seed = f"{source_path or ''}#{chunk_index if chunk_index is not None else 0}"
    return uuid5(_ENTRY_ID_NAMESPACE, seed)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/search", response_model=SearchResponse)
async def search_knowledge(
    request: Request,
    query: str = Query(..., min_length=1, description="Search query"),
    knowledge_type: KnowledgeType | None = Query(
        default=None, alias="knowledgeType", description="Filter by knowledge type"
    ),
    limit: int = Query(default=5, ge=1, le=50, description="Max results"),
) -> SearchResponse:
    """Semantic search over indexed knowledge.

    Routes through ``KnowledgeService`` when available so it shares
    the same backend as ``/ingest`` and ``/documents`` (MET-390).
    """
    with tracer.start_as_current_span("knowledge_api.search") as span:
        span.set_attribute("knowledge.query_length", len(query))

        service = _maybe_service(request)
        if service is not None:
            hits = await service.search(
                query=query,
                top_k=limit,
                knowledge_type=knowledge_type,
            )
            now = datetime.now(UTC)
            results = [
                KnowledgeEntryResponse(
                    id=_entry_id_for(h.source_path, h.chunk_index),
                    content=h.content,
                    knowledgeType=h.knowledge_type or KnowledgeType.DESIGN_DECISION,
                    metadata=h.metadata,
                    sourceWorkProductId=h.source_work_product_id,
                    sourcePath=h.source_path,
                    chunkIndex=h.chunk_index,
                    totalChunks=h.total_chunks,
                    createdAt=now,
                )
                for h in hits
            ]
            logger.info(
                "knowledge_search",
                query=query[:80],
                result_count=len(results),
                backend="knowledge_service",
            )
            return SearchResponse(
                results=results,
                query=query,
                totalFound=len(results),
            )

        # Legacy path — direct ``KnowledgeStore`` access. Used by unit
        # tests that don't wire ``knowledge_service``.
        store = _get_store(request)
        embedding_svc = _get_embedding(request)

        query_embedding = await embedding_svc.embed(query)
        entries = await store.search(
            embedding=query_embedding,
            knowledge_type=knowledge_type,
            limit=limit,
        )

        results = [
            KnowledgeEntryResponse(
                id=e.id,
                content=e.content,
                knowledgeType=e.knowledge_type,
                metadata=e.metadata,
                sourceWorkProductId=e.source_work_product_id,
                sourcePath=e.source_path,
                chunkIndex=e.chunk_index,
                totalChunks=e.total_chunks,
                createdAt=e.created_at,
            )
            for e in entries
        ]
        logger.info(
            "knowledge_search",
            query=query[:80],
            result_count=len(results),
            backend="knowledge_store",
        )
        return SearchResponse(
            results=results,
            query=query,
            totalFound=len(results),
        )


def _get_knowledge_service(request: Request) -> Any:
    svc = getattr(request.app.state, "knowledge_service", None)
    if svc is None:
        raise HTTPException(
            status_code=503,
            detail="KnowledgeService not initialized; set DATABASE_URL and restart.",
        )
    return svc


@router.post(
    "/documents",
    response_model=IngestDocumentResponse,
    status_code=201,
)
async def ingest_document(
    request: Request,
    body: IngestDocumentRequest,
) -> IngestDocumentResponse:
    """Ingest a document (markdown / plain text) via the L1 ``KnowledgeService``.

    Backs the ``forge ingest <path>`` CLI (MET-336). Heading-aware
    chunking, dedup, and citation metadata are handled by the
    underlying provider — the route is a thin pass-through.
    """
    with tracer.start_as_current_span("knowledge_api.ingest_document") as span:
        span.set_attribute("knowledge.source_path", body.source_path)
        span.set_attribute("knowledge.type", str(body.knowledge_type))
        service = _get_knowledge_service(request)
        result = await service.ingest(
            content=body.content,
            source_path=body.source_path,
            knowledge_type=body.knowledge_type,
            source_work_product_id=body.source_work_product_id,
            metadata=body.metadata or None,
        )
        logger.info(
            "knowledge_document_ingested",
            source_path=body.source_path,
            chunks=result.chunks_indexed,
        )
        return IngestDocumentResponse(
            entryIds=list(result.entry_ids),
            chunksIndexed=result.chunks_indexed,
            sourcePath=result.source_path,
        )


@router.post("/ingest", response_model=IngestResponse, status_code=201)
async def ingest_knowledge(
    request: Request,
    body: IngestRequest,
) -> IngestResponse:
    """Manually ingest a knowledge entry.

    Routes through ``KnowledgeService`` when available so the chunk
    pipeline, dedup, and citation-field round-trip apply consistently
    with ``/documents`` and ``/search`` (MET-390).
    """
    with tracer.start_as_current_span("knowledge_api.ingest") as span:
        span.set_attribute("knowledge.type", str(body.knowledge_type))

        service = _maybe_service(request)
        if service is not None:
            # Service-backed ingest: chunks the content, populates
            # citation fields, triggers predelete on re-ingest. Mint
            # a synthetic source_path when caller didn't supply one
            # so dedup keys stay stable per-content (uuid5 of body).
            source_path = body.source_path or (
                f"manual://{uuid5(_ENTRY_ID_NAMESPACE, body.content)}"
            )
            try:
                result = await service.ingest(
                    content=body.content,
                    source_path=source_path,
                    knowledge_type=body.knowledge_type,
                    source_work_product_id=body.source_work_product_id,
                    metadata=body.metadata or None,
                )
            except ValueError as exc:
                # KnowledgeService raises on empty content (MET-375).
                span.record_exception(exc)
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            entry_id = (
                result.entry_ids[0] if result.entry_ids else uuid5(_ENTRY_ID_NAMESPACE, source_path)
            )
            logger.info(
                "knowledge_ingested",
                entry_id=str(entry_id),
                embedded=result.chunks_indexed > 0,
                chunks=result.chunks_indexed,
                backend="knowledge_service",
            )
            return IngestResponse(entryId=entry_id, embedded=result.chunks_indexed > 0)

        # Legacy path — direct ``KnowledgeStore`` write. Kept for unit
        # tests that don't wire ``knowledge_service``.
        store = _get_store(request)
        embedding_svc = _get_embedding(request)

        from digital_twin.knowledge.store import KnowledgeEntry

        embedded = False
        embedding: list[float] = []
        try:
            embedding = await embedding_svc.embed(body.content)
            embedded = len(embedding) > 0 and any(v != 0.0 for v in embedding)
        except Exception as exc:
            span.record_exception(exc)
            logger.warning("knowledge_ingest_embed_failed", error=str(exc))

        entry = KnowledgeEntry(
            content=body.content,
            embedding=embedding,
            knowledge_type=body.knowledge_type,
            metadata=body.metadata,
            source_work_product_id=body.source_work_product_id,
            source_path=body.source_path,
        )
        stored = await store.store(entry)
        logger.info(
            "knowledge_ingested",
            entry_id=str(stored.id),
            embedded=embedded,
            backend="knowledge_store",
        )
        return IngestResponse(entryId=stored.id, embedded=embedded)


@router.get("/{entry_id}", response_model=KnowledgeEntryResponse)
async def get_knowledge_entry(
    request: Request,
    entry_id: UUID,
) -> KnowledgeEntryResponse:
    """Retrieve a single knowledge entry by ID."""
    store = _get_store(request)
    entry = await store.get(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Knowledge entry not found")
    return KnowledgeEntryResponse(
        id=entry.id,
        content=entry.content,
        knowledgeType=entry.knowledge_type,
        metadata=entry.metadata,
        sourceWorkProductId=entry.source_work_product_id,
        sourcePath=entry.source_path,
        chunkIndex=entry.chunk_index,
        totalChunks=entry.total_chunks,
        createdAt=entry.created_at,
    )
