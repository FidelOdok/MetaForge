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

    model_config = {"populate_by_name": True}


class IngestResponse(BaseModel):
    """Response from the knowledge ingest endpoint."""

    entry_id: UUID = Field(alias="entryId")
    embedded: bool

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
        createdAt=entry.created_at,
    )
