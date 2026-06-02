"""Knowledge API routes for semantic search and ingestion.

Endpoints live under ``/v1/knowledge`` (MET-451 — was ``/api/v1/knowledge``,
moved to align with the rest of the gateway and so dashboard's nginx
proxy that strips ``/api/`` from inbound requests reaches us).

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

router = APIRouter(prefix="/v1/knowledge", tags=["knowledge"])


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
    # MET-485: scope the ingest to a project tenant (mirrors the
    # ``projectId`` filter on /search and /sources). Absent → default tenant.
    project_id: UUID | None = Field(default=None, alias="projectId")

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
    # MET-485: scope the ingest to a project tenant. Absent → default.
    project_id: UUID | None = Field(default=None, alias="projectId")

    model_config = {"populate_by_name": True}


class IngestDocumentResponse(BaseModel):
    """L1 ingest result — mirrors ``IngestResult`` (MET-336)."""

    entry_ids: list[UUID] = Field(alias="entryIds")
    chunks_indexed: int = Field(alias="chunksIndexed")
    source_path: str = Field(alias="sourcePath")

    model_config = {"populate_by_name": True}


class SourceSummaryResponse(BaseModel):
    """One row from ``GET /knowledge/sources`` — mirrors ``SourceSummary`` (MET-411)."""

    source_path: str = Field(alias="sourcePath")
    knowledge_type: str | None = Field(default=None, alias="knowledgeType")
    fragment_count: int = Field(alias="fragmentCount")
    indexed_at: datetime = Field(alias="indexedAt")
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}


class SourceListResponse(BaseModel):
    """Envelope for ``GET /knowledge/sources``."""

    sources: list[SourceSummaryResponse]
    total: int

    model_config = {"populate_by_name": True}


class SourceDetailResponse(SourceSummaryResponse):
    """Per-source detail — adds an empty ``chunks`` list for parity with the MCP resource."""

    chunks: list[dict[str, Any]] = Field(default_factory=list)


class SourceDeleteResponse(BaseModel):
    """Envelope for ``DELETE /knowledge/sources/{path}``."""

    source_path: str = Field(alias="sourcePath")
    deleted_chunks: int = Field(alias="deletedChunks")

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
            project_id=body.project_id,
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
                    project_id=body.project_id,
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
                source_path=source_path,
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
            source_path=body.source_path,
            backend="knowledge_store",
        )
        return IngestResponse(entryId=stored.id, embedded=embedded)


def _summary_to_response(summary: Any) -> SourceSummaryResponse:
    """Project a ``SourceSummary`` dataclass onto the wire model.

    ``knowledge_type`` may be a ``KnowledgeType`` enum, a string, or
    ``None`` — coerce to the bare string form so JSON consumers don't
    have to branch.
    """
    kt = summary.knowledge_type
    if kt is None:
        kt_str: str | None = None
    elif isinstance(kt, KnowledgeType):
        kt_str = str(kt)
    else:
        kt_str = str(kt)
    return SourceSummaryResponse(
        sourcePath=summary.source_path,
        knowledgeType=kt_str,
        fragmentCount=summary.fragment_count,
        indexedAt=summary.indexed_at,
        metadata=dict(summary.metadata or {}),
    )


@router.get("/sources", response_model=SourceListResponse)
async def list_knowledge_sources(
    request: Request,
    knowledge_type: KnowledgeType | None = Query(
        default=None, alias="knowledgeType", description="Filter by knowledge type"
    ),
    project_id: UUID | None = Query(
        default=None, alias="projectId", description="Filter by project UUID"
    ),
    limit: int = Query(default=100, ge=1, le=1000, description="Max sources to return"),
    offset: int = Query(default=0, ge=0, description="Pagination offset"),
) -> SourceListResponse:
    """List ingested knowledge sources via ``KnowledgeService.list_sources()``.

    Backs the ``forge sources list`` CLI (MET-411). Mirrors the schema
    surfaced by the ``metaforge://knowledge/sources`` MCP resource.
    """
    with tracer.start_as_current_span("knowledge_api.list_sources") as span:
        if knowledge_type is not None:
            span.set_attribute("knowledge.type", str(knowledge_type))
        if project_id is not None:
            span.set_attribute("knowledge.project_id", str(project_id))
        service = _get_knowledge_service(request)
        summaries = await service.list_sources(
            project_id=project_id,
            knowledge_type=knowledge_type,
            limit=limit,
            offset=offset,
        )
        rows = [_summary_to_response(s) for s in summaries]
        span.set_attribute("knowledge.result_count", len(rows))
        logger.info(
            "knowledge_sources_listed",
            count=len(rows),
            knowledge_type=str(knowledge_type) if knowledge_type else None,
            project_id=str(project_id) if project_id else None,
        )
        return SourceListResponse(sources=rows, total=len(rows))


@router.get("/sources/{source_path:path}", response_model=SourceDetailResponse)
async def get_knowledge_source(
    request: Request,
    source_path: str,
    project_id: UUID | None = Query(
        default=None, alias="projectId", description="Filter by project UUID"
    ),
) -> SourceDetailResponse:
    """Per-source detail — looks up by exact ``source_path`` match.

    No dedicated single-source accessor exists in the ``KnowledgeService``
    contract, so we list and find — fine for CLI usage where the user
    has already picked a known path. Returns 404 when the source isn't
    registered.
    """
    with tracer.start_as_current_span("knowledge_api.get_source") as span:
        span.set_attribute("knowledge.source_path", source_path)
        service = _get_knowledge_service(request)
        # Page through up to 1000 sources to find the match. The CLI
        # workflow targets recently-ingested sources so this is fine
        # for Phase 1; a dedicated accessor lands separately.
        summaries = await service.list_sources(project_id=project_id, limit=1000)
        match = next((s for s in summaries if s.source_path == source_path), None)
        if match is None:
            span.set_attribute("knowledge.not_found", True)
            logger.info("knowledge_source_not_found", source_path=source_path)
            raise HTTPException(
                status_code=404,
                detail=f"No knowledge source registered for {source_path!r}",
            )
        base = _summary_to_response(match)
        return SourceDetailResponse(
            sourcePath=base.source_path,
            knowledgeType=base.knowledge_type,
            fragmentCount=base.fragment_count,
            indexedAt=base.indexed_at,
            metadata=base.metadata,
            chunks=[],
        )


@router.delete("/sources/{source_path:path}", response_model=SourceDeleteResponse)
async def delete_knowledge_source(
    request: Request,
    source_path: str,
) -> SourceDeleteResponse:
    """Delete every chunk for a source via ``KnowledgeService.delete_by_source()``.

    Backs the ``forge sources delete`` CLI (MET-411). Returns the
    chunk count the backend removed; ``0`` when the source was already
    absent — callers treat that as a no-op rather than an error.
    """
    with tracer.start_as_current_span("knowledge_api.delete_source") as span:
        span.set_attribute("knowledge.source_path", source_path)
        service = _get_knowledge_service(request)
        deleted = await service.delete_by_source(source_path)
        span.set_attribute("knowledge.deleted_chunks", int(deleted))
        logger.info(
            "knowledge_source_deleted",
            source_path=source_path,
            deleted_chunks=int(deleted),
        )
        return SourceDeleteResponse(sourcePath=source_path, deletedChunks=int(deleted))


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
