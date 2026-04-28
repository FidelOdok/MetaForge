"""Knowledge API routes for semantic search and ingestion.

Endpoints live under ``/api/v1/knowledge``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from digital_twin.knowledge.store import KnowledgeType
from observability.tracing import get_tracer

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
    """Semantic search over indexed knowledge."""
    with tracer.start_as_current_span("knowledge_api.search") as span:
        span.set_attribute("knowledge.query_length", len(query))
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
        logger.info("knowledge_search", query=query[:80], result_count=len(results))
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
    """Manually ingest a knowledge entry."""
    with tracer.start_as_current_span("knowledge_api.ingest") as span:
        span.set_attribute("knowledge.type", str(body.knowledge_type))
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
