"""``/v1/projects/{project_id}/approval-policy`` (FORGE-726).

Reading the policy is open to every caller. Changing it is a person's
decision: a request that identifies itself as an agent is refused, no MCP
tool sets it, and a mode above the operator cap (``METAFORGE_AUTO_APPROVE_MAX``)
is refused rather than quietly lowered, so the person sees why.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from api_gateway.approvals.policy import (
    MAX_AUTO_HOURS,
    MODES,
    ApprovalPolicy,
    auto_expiry,
    effective_mode,
    get_policy_store,
    max_mode,
    mode_allowed,
)
from api_gateway.approvals.routes import surface_from_request
from api_gateway.approvals.schemas import GateReview
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.approvals.policy_routes")

router = APIRouter(prefix="/v1/projects", tags=["approvals"])


class ApprovalPolicyView(BaseModel):
    """The stored policy, what is in force now, and the operator cap."""

    policy: ApprovalPolicy
    effective_mode: str
    max_mode: str
    modes: list[str]


class ApprovalPolicyUpdate(BaseModel):
    mode: Literal["off", "advise", "auto_clean", "auto"]
    reviewer: Literal["server", "client", "client_then_server"] = "server"
    reviewer_model: str = Field(default="", max_length=200)
    #: ``auto`` only: how long it lasts (default 24 h, at most a week).
    expires_in_hours: float | None = Field(default=None, gt=0, le=MAX_AUTO_HOURS)


def _view(policy: ApprovalPolicy) -> ApprovalPolicyView:
    return ApprovalPolicyView(
        policy=policy, effective_mode=effective_mode(policy), max_mode=max_mode(), modes=list(MODES)
    )


@router.get("/{project_id}/approval-policy", response_model=ApprovalPolicyView)
async def get_approval_policy(project_id: str) -> ApprovalPolicyView:
    """Who decides this project's gates right now."""
    return _view(await get_policy_store().get(project_id))


@router.put("/{project_id}/approval-policy", response_model=ApprovalPolicyView)
async def put_approval_policy(
    project_id: str, body: ApprovalPolicyUpdate, request: Request
) -> ApprovalPolicyView:
    """Set who decides this project's gates. A person's decision only."""
    from api_gateway.auth.dependencies import current_principal

    surface, _ = surface_from_request(request)
    if surface == "agent":
        raise HTTPException(
            status_code=403,
            detail="changing the approval policy is a person's decision; an agent cannot set it",
        )
    if not mode_allowed(body.mode):
        raise HTTPException(
            status_code=422,
            detail=f"mode '{body.mode}' is above this gateway's cap '{max_mode()}' "
            "(METAFORGE_AUTO_APPROVE_MAX)",
        )
    principal = current_principal(request)
    who = f"user:{principal.subject}" if principal is not None else f"local:{surface}"
    with tracer.start_as_current_span("approval_policy.put") as span:
        span.set_attribute("project.id", project_id)
        span.set_attribute("policy.mode", body.mode)
        policy = ApprovalPolicy(
            project_id=project_id,
            mode=body.mode,
            reviewer=body.reviewer,
            reviewer_model=body.reviewer_model.strip(),
            expires_at=auto_expiry(body.expires_in_hours) if body.mode == "auto" else None,
            updated_by=who,
            updated_at=datetime.now(UTC).isoformat(),
        )
        await get_policy_store().put(policy)
    logger.info(
        "approval_policy_set",
        project_id=project_id,
        mode=body.mode,
        reviewer=body.reviewer,
        reviewer_model=policy.reviewer_model,
        expires_at=policy.expires_at,
        by=who,
    )
    return _view(policy)


review_router = APIRouter(prefix="/v1/runs", tags=["approvals"])


@review_router.post("/{run_id}/gate-review", response_model=GateReview)
async def post_gate_review(run_id: str, body: GateReview, request: Request) -> GateReview:
    """Record a reviewer agent's note on the run's open gate (FORGE-726).

    Called by the design-flow worker's review step (or on behalf of a client
    reviewer). The note is stored on the run and shown with the gate; the
    project's approval policy then decides what, if anything, it does: in
    ``off`` and ``advise`` nothing, in ``auto_clean`` and ``auto`` it may decide
    the gate as ``reviewer:<source>:<model>``, never as a person.
    """
    from api_gateway.approvals import service
    from orchestrator.harness.runs import RunNotFoundError

    with tracer.start_as_current_span("approval_policy.gate_review") as span:
        span.set_attribute("run.id", run_id)
        span.set_attribute("review.recommendation", body.recommendation)
        try:
            return await service.record_review(request.app, run_id, body)
        except RunNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"run '{run_id}' not found") from exc
