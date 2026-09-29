"""Maturity-gate promotion API (FORGE-290, gap G-G4).

``POST /v1/promotion/attempt`` is a thin REST wrapper over
``api_gateway.requirement_intelligence.promotion.attempt_promotion``
(FORGE-319) -- the same evidence-gated approval every MCP caller already
goes through, exposed so the dashboard's Gate review section doesn't need
an MCP client of its own for one create action (same precedent as
``POST /v1/requirements/constraints``, ``POST /v1/design-loop/start``).

``GET /v1/promotion?project_id=...`` lists a project's ``MaturityGate``
history -- wraps the already-existing ``twin.list_maturity_gates`` API
method, which had no consumer of any kind before this ticket.
"""

from __future__ import annotations

from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api_gateway.requirement_intelligence.promotion import attempt_promotion
from observability.tracing import get_tracer
from twin_core.api import InMemoryTwinAPI

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.promotion.routes")

_twin: InMemoryTwinAPI = InMemoryTwinAPI.create()


def init_twin(twin: object) -> None:
    """Replace the default in-memory twin with the orchestrator's twin."""
    global _twin  # noqa: PLW0603
    _twin = twin  # type: ignore[assignment]
    logger.info("promotion_twin_initialized", twin_type=type(twin).__name__)


router = APIRouter(prefix="/v1/promotion", tags=["promotion"])


class AttemptPromotionRequest(BaseModel):
    projectId: str  # noqa: N815 -- dashboard contract is camelCase
    level: str
    requiredClaimIds: list[str]  # noqa: N815
    k: float = 1.0
    decidedBy: str | None = None  # noqa: N815
    comment: str | None = None
    reject: bool = False


class RequiredClaimResultView(BaseModel):
    requirementId: str  # noqa: N815
    requirementName: str  # noqa: N815
    decision: str
    detail: str
    waiverId: str | None  # noqa: N815


class AttemptPromotionResponse(BaseModel):
    gateId: str  # noqa: N815
    level: str
    promoted: bool
    blockedReason: str | None  # noqa: N815
    decidedBy: str | None  # noqa: N815
    comment: str | None
    results: list[RequiredClaimResultView]


@router.post("/attempt", response_model=AttemptPromotionResponse)
async def attempt_promotion_route(payload: AttemptPromotionRequest) -> AttemptPromotionResponse:
    with tracer.start_as_current_span("promotion.attempt") as span:
        span.set_attribute("promotion.project_id", payload.projectId)
        span.set_attribute("promotion.level", payload.level)
        try:
            result = await attempt_promotion(
                _twin,
                project_id=payload.projectId,
                level=payload.level,
                required_claim_ids=payload.requiredClaimIds,
                k=payload.k,
                decided_by=payload.decidedBy,
                comment=payload.comment,
                reject=payload.reject,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.info(
        "promotion_attempted_via_dashboard",
        project_id=payload.projectId,
        level=payload.level,
        promoted=result["promoted"],
    )
    return AttemptPromotionResponse(
        gateId=result["gate_id"],
        level=result["level"],
        promoted=result["promoted"],
        blockedReason=result["blocked_reason"],
        decidedBy=result["decided_by"],
        comment=result["comment"],
        results=[RequiredClaimResultView(**r) for r in result["results"]],
    )


class MaturityGateSummary(BaseModel):
    gateId: str  # noqa: N815
    level: str
    promoted: bool
    blockedReason: str | None  # noqa: N815
    decidedBy: str | None  # noqa: N815
    comment: str | None
    createdAt: str  # noqa: N815


class PromotionHistoryResponse(BaseModel):
    gates: list[MaturityGateSummary]


@router.get("")
async def list_promotions(project_id: str) -> PromotionHistoryResponse:
    try:
        pid = UUID(project_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid project_id") from exc
    with tracer.start_as_current_span("promotion.list"):
        gates = await _twin.list_maturity_gates(project_id=pid)
    gates.sort(key=lambda g: g.created_at, reverse=True)
    return PromotionHistoryResponse(
        gates=[
            MaturityGateSummary(
                gateId=str(g.id),
                level=g.level.value,
                promoted=g.promoted,
                blockedReason=g.blocked_reason,
                decidedBy=g.decided_by,
                comment=g.comment,
                createdAt=g.created_at.isoformat(),
            )
            for g in gates
        ]
    )
