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

import structlog
from fastapi import APIRouter, HTTPException, Request

from api_gateway.memory.schemas import (
    MemoryHitResponse,
    MemoryRetrieveRequest,
    MemoryRetrieveResponse,
)
from digital_twin.memory.client import MemoryClient
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
        )

        span.set_attribute("memory.result_count", len(hits))
        logger.info(
            "memory_api_retrieve",
            goal_length=len(payload.goal),
            limit=payload.limit,
            result_count=len(hits),
            project_id=str(payload.project_id) if payload.project_id else None,
            agent_code=payload.agent_code,
        )

        return MemoryRetrieveResponse(
            hits=[_hit_to_response(h) for h in hits],
            query=payload.goal,
            total_found=len(hits),
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
