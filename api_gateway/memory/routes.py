"""Memory API routes — POST /v1/memory/retrieve (MET-453).

Wraps ``digital_twin.memory.client.MemoryClient`` so the dashboard / CLI
can pull similar past experiences without speaking MCP. The endpoint
delegates the embedding + cosine search to the client; this layer only
handles wire decoding and project-scope plumbing.

The gateway init wires the client on ``app.state.memory_client`` (see
``api_gateway.server._init_knowledge_store``). When the embedding
service or the store fail to initialise — local-only dev without
sentence-transformers, for example — the route degrades to HTTP 503 so
clients can fall back to keyword search instead of seeing opaque 500s.
"""

from __future__ import annotations

from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException, Query, Request

from api_gateway.memory.schemas import (
    ConsolidationTriggerRequest,
    ConsolidationTriggerResponse,
    InsightListResponse,
    InsightResponse,
    KnowledgeHitResponse,
    MemoryHitResponse,
    MemoryRetrieveRequest,
    MemoryRetrieveResponse,
    MemorySearchRequest,
    MemorySearchResponse,
)
from digital_twin.knowledge.service import SearchHit
from digital_twin.memory.client import MemoryClient
from digital_twin.memory.consolidation.insight import Insight, InsightStatus
from digital_twin.memory.consolidation.modes import (
    ConsolidationModeError,
    ConsolidationRunRequest,
)
from digital_twin.memory.consolidation.orchestrator import (
    ConsolidationOrchestrator,
)
from digital_twin.memory.consolidation.themes import ConsolidationTheme
from digital_twin.memory.consolidation.writer import InsightStore
from digital_twin.memory.models import MemorySearchHit
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.memory")

router = APIRouter(prefix="/v1/memory", tags=["memory"])


def _get_client(request: Request) -> MemoryClient:
    client = getattr(request.app.state, "memory_client", None)
    if client is None:
        raise HTTPException(
            status_code=503,
            detail="memory_client_not_ready",
        )
    if not isinstance(client, MemoryClient):
        raise HTTPException(
            status_code=503,
            detail="memory_client_misconfigured",
        )
    return client


@router.post("/retrieve", response_model=MemoryRetrieveResponse)
async def retrieve_similar_experience(
    payload: MemoryRetrieveRequest,
    request: Request,
) -> MemoryRetrieveResponse:
    """Return experiences most similar to the supplied goal.

    Body shape matches ``MemoryRetrieveRequest``. The response carries
    ``hits`` sorted by descending similarity, plus the echoed ``query``
    and ``total_found`` so callers can paginate without re-derivation.
    """
    client = _get_client(request)
    with tracer.start_as_current_span("memory.api.retrieve") as span:
        span.set_attribute("memory.goal_length", len(payload.goal))
        span.set_attribute("memory.limit", payload.limit)
        if payload.project_id is not None:
            span.set_attribute("memory.project_id", str(payload.project_id))

        hits = await client.retrieve_similar_experience(
            payload.goal,
            limit=payload.limit,
            project_id=payload.project_id,
            agent_code=payload.agent_code,
            only_success=payload.only_success,
            min_similarity=payload.min_similarity,
        )

        span.set_attribute("memory.result_count", len(hits))
        logger.info(
            "memory_api_retrieve",
            goal_length=len(payload.goal),
            limit=payload.limit,
            result_count=len(hits),
            project_id=str(payload.project_id) if payload.project_id else None,
            agent_code=payload.agent_code,
            min_similarity=payload.min_similarity,
        )

        return MemoryRetrieveResponse(
            hits=[_hit_to_response(h) for h in hits],
            query=payload.goal,
            total_found=len(hits),
        )


def _get_orchestrator(request: Request) -> ConsolidationOrchestrator:
    orchestrator = getattr(request.app.state, "consolidation_orchestrator", None)
    if orchestrator is None:
        raise HTTPException(
            status_code=503,
            detail="consolidation_orchestrator_not_ready",
        )
    if not isinstance(orchestrator, ConsolidationOrchestrator):
        raise HTTPException(
            status_code=503,
            detail="consolidation_orchestrator_misconfigured",
        )
    return orchestrator


@router.post("/consolidate", response_model=ConsolidationTriggerResponse)
async def trigger_consolidation(
    payload: ConsolidationTriggerRequest,
    request: Request,
) -> ConsolidationTriggerResponse:
    """Trigger one consolidation pass synchronously.

    Defaults to ``on_demand`` mode (manual triage with the importance
    floor relaxed). Pass ``mode=background`` to run the standard pass
    or ``mode=janitor`` to re-validate previously-stored insights
    without synthesizing new ones.
    """
    orchestrator = _get_orchestrator(request)
    try:
        run_request = ConsolidationRunRequest(
            mode=payload.mode,
            since=payload.since,
            until=payload.until,
            project_id=payload.project_id,
            theme=payload.theme,
            min_importance=payload.min_importance,
            fetch_limit=payload.fetch_limit,
        )
    except ConsolidationModeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    with tracer.start_as_current_span("memory.api.consolidate") as span:
        span.set_attribute("memory.mode", payload.mode.value)
        if payload.project_id is not None:
            span.set_attribute("memory.project_id", str(payload.project_id))

        report = await orchestrator.run_request(run_request)

        span.set_attribute("memory.accepted_count", report.accepted_count)
        span.set_attribute("memory.rejected_count", report.rejected_count)
        logger.info(
            "memory_api_consolidate",
            mode=payload.mode.value,
            fetched=report.fetched_count,
            accepted=report.accepted_count,
            rejected=report.rejected_count,
        )

        return ConsolidationTriggerResponse(
            mode=report.mode,
            fetched_count=report.fetched_count,
            group_count=report.group_count,
            synthesized_count=report.synthesized_count,
            accepted_count=report.accepted_count,
            rejected_count=report.rejected_count,
            revalidated_count=report.revalidated_count,
            newly_failed_count=report.newly_failed_count,
            rejected_reasons=report.rejected_reasons,
        )


def _get_insight_store(request: Request) -> InsightStore:
    store = getattr(request.app.state, "consolidation_insight_store", None)
    if store is None:
        raise HTTPException(
            status_code=503,
            detail="consolidation_insight_store_not_ready",
        )
    if not isinstance(store, InsightStore):
        raise HTTPException(
            status_code=503,
            detail="consolidation_insight_store_misconfigured",
        )
    return store


@router.get("/insights", response_model=InsightListResponse)
async def list_insights(
    request: Request,
    theme: ConsolidationTheme | None = Query(default=None),
    include_stale: bool = Query(default=False, alias="includeStale"),
    limit: int = Query(default=50, ge=1, le=500),
) -> InsightListResponse:
    """List consolidated insights, newest first.

    Excludes ``STALE_WARN`` insights by default — agents should act on
    fresh lessons. Pass ``includeStale=true`` for an audit / review view
    that includes faded insights. Optional ``theme`` narrows to one
    consolidation theme.
    """
    store = _get_insight_store(request)
    with tracer.start_as_current_span("memory.api.list_insights") as span:
        span.set_attribute("memory.include_stale", include_stale)
        if theme is not None:
            span.set_attribute("memory.theme", theme.value)

        # Fetch a bit extra so status-filtering doesn't under-fill the page.
        raw = await store.list(theme=theme, limit=limit if include_stale else limit * 2)
        if include_stale:
            filtered = raw[:limit]
        else:
            filtered = [i for i in raw if i.status is not InsightStatus.STALE_WARN][:limit]

        span.set_attribute("memory.result_count", len(filtered))
        logger.info(
            "memory_api_list_insights",
            theme=theme.value if theme else None,
            include_stale=include_stale,
            result_count=len(filtered),
        )
        return InsightListResponse(
            insights=[_insight_to_response(i) for i in filtered],
            total=len(filtered),
            theme=theme,
            include_stale=include_stale,
        )


def _insight_to_response(insight: Insight) -> InsightResponse:
    return InsightResponse(
        id=insight.id,
        theme=insight.theme,
        kind=insight.kind,
        narrative=insight.narrative,
        confidence=insight.confidence,
        confidence_tier=insight.confidence_tier,
        status=insight.status,
        supporting_experience_ids=list(insight.supporting_experience_ids),
        synthesized_at=insight.synthesized_at,
    )


# ---------------------------------------------------------------------------
# MET-471 — knowledge-backed convenience endpoints
# ---------------------------------------------------------------------------


@router.post("/search", response_model=MemorySearchResponse)
async def search_design_rationale(
    payload: MemorySearchRequest,
    request: Request,
) -> MemorySearchResponse:
    """Semantic search over design-decision knowledge (MET-471).

    Wraps ``MemoryClient.search_design_rationale``: a typed convenience
    over the L1 knowledge base that asks "why was X decided?" and
    returns ranked hits keyed off ``KnowledgeType.DESIGN_DECISION``.
    Requires the gateway to have wired a knowledge_service on
    ``app.state.knowledge_service``; the 503 from ``_get_client``
    covers the case where the service hasn't initialised.
    """
    client = _get_client(request)
    with tracer.start_as_current_span("memory.api.search") as span:
        span.set_attribute("memory.query_length", len(payload.query))
        span.set_attribute("memory.limit", payload.limit)
        if payload.project_id is not None:
            span.set_attribute("memory.project_id", str(payload.project_id))

        try:
            hits = await client.search_design_rationale(
                payload.query,
                limit=payload.limit,
                project_id=payload.project_id,
            )
        except RuntimeError as exc:
            # MemoryClient raises when knowledge_service wasn't wired.
            raise HTTPException(
                status_code=503,
                detail="memory_client_knowledge_service_not_ready",
            ) from exc

        span.set_attribute("memory.result_count", len(hits))
        logger.info(
            "memory_api_search",
            query_length=len(payload.query),
            limit=payload.limit,
            result_count=len(hits),
            project_id=str(payload.project_id) if payload.project_id else None,
        )
        return MemorySearchResponse(
            hits=[_knowledge_hit_to_response(h) for h in hits],
            query=payload.query,
            total_found=len(hits),
        )


@router.get("/components/{name}", response_model=MemorySearchResponse)
async def get_component_context(
    name: str,
    request: Request,
    limit: int = Query(default=5, ge=1, le=50),
    project_id: UUID | None = Query(default=None, alias="projectId"),
) -> MemorySearchResponse:
    """Component usage / relationship knowledge for ``name`` (MET-471).

    Wraps ``MemoryClient.get_component_context``: a typed convenience
    over the L1 knowledge base keyed off ``KnowledgeType.COMPONENT``.
    Empty / whitespace ``name`` returns 422 — that's a client bug, not
    a backend gap.
    """
    if not name or not name.strip():
        raise HTTPException(
            status_code=422,
            detail="component name must be non-empty",
        )

    client = _get_client(request)
    with tracer.start_as_current_span("memory.api.get_component") as span:
        span.set_attribute("memory.component_name", name)
        span.set_attribute("memory.limit", limit)
        if project_id is not None:
            span.set_attribute("memory.project_id", str(project_id))

        try:
            hits = await client.get_component_context(
                name,
                limit=limit,
                project_id=project_id,
            )
        except RuntimeError as exc:
            raise HTTPException(
                status_code=503,
                detail="memory_client_knowledge_service_not_ready",
            ) from exc

        span.set_attribute("memory.result_count", len(hits))
        logger.info(
            "memory_api_get_component",
            component_name=name,
            limit=limit,
            result_count=len(hits),
            project_id=str(project_id) if project_id else None,
        )
        return MemorySearchResponse(
            hits=[_knowledge_hit_to_response(h) for h in hits],
            query=name,
            total_found=len(hits),
        )


def _knowledge_hit_to_response(hit: SearchHit) -> KnowledgeHitResponse:
    return KnowledgeHitResponse(
        content=hit.content,
        similarity_score=hit.similarity_score,
        source_path=hit.source_path,
        heading=hit.heading,
        chunk_index=hit.chunk_index,
        total_chunks=hit.total_chunks,
        knowledge_type=hit.knowledge_type.value if hit.knowledge_type else None,
        source_work_product_id=hit.source_work_product_id,
    )


def _hit_to_response(hit: MemorySearchHit) -> MemoryHitResponse:
    exp = hit.experience
    return MemoryHitResponse(
        experience_id=exp.id,
        similarity=hit.similarity,
        rank=hit.rank,
        agent_code=exp.agent_code,
        task_type=exp.task_type,
        run_id=exp.run_id,
        step_id=exp.step_id,
        success=exp.success,
        duration_seconds=exp.duration_seconds,
        result_summary=exp.result_summary,
        error=exp.error,
        importance=exp.importance,
        confidence=exp.confidence,
        timestamp=exp.timestamp,
        project_id=exp.project_id,
    )
