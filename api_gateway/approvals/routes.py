"""``/v1/approvals``: list, inspect and decide every kind of approval (FORGE-507).

The older per-kind routes keep working; they and this API call the same
decision functions, so the rules are one body of code.
"""

from __future__ import annotations

from typing import Literal

import structlog
from fastapi import APIRouter, HTTPException, Request

from api_gateway.approvals import service
from api_gateway.approvals.schemas import (
    ID_PREFIXES,
    ApprovalItem,
    ApprovalKind,
    ApprovalListResponse,
    DecisionRequest,
    Surface,
)
from api_gateway.auth.approver import approver_from_request
from mcp_core.guardrails import Approver
from observability.metrics import MetricsCollector, collector_for

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/v1/approvals", tags=["approvals"])

SURFACE_HEADER = "X-MetaForge-Surface"
ON_BEHALF_OF_HEADER = "X-MetaForge-On-Behalf-Of"
AGENT_HEADER = "X-MetaForge-Agent"
_SURFACES = ("dashboard", "cli", "agent")
_IGNORED_BODY_IDENTITY = ("reviewer", "approved_by", "approvedBy")

_metrics: MetricsCollector | None = None


def set_metrics(metrics: MetricsCollector | None) -> None:
    """Inject a collector (tests), or ``None`` to resolve it lazily again."""
    global _metrics  # noqa: PLW0603
    _metrics = metrics


def _collector() -> MetricsCollector:
    global _metrics  # noqa: PLW0603
    if _metrics is None:
        _metrics = collector_for("metaforge-gateway")
    return _metrics


def surface_from_request(request: Request) -> tuple[Surface, str | None]:
    """The client surface and, for an agent, whom it acts for.

    A missing surface is ``unknown``, not a guess. An unrecognised one is a
    422: recording "dashboard" for a typo would be a false audit entry.
    """
    raw = (request.headers.get(SURFACE_HEADER) or "").strip().lower()
    on_behalf = (request.headers.get(ON_BEHALF_OF_HEADER) or "").strip() or None
    if not raw:
        surface: Surface = "unknown"
    elif raw in _SURFACES:
        surface = raw  # type: ignore[assignment]
    else:
        raise HTTPException(
            status_code=422,
            detail=f"{SURFACE_HEADER} must be one of {', '.join(_SURFACES)}; got '{raw}'",
        )
    if on_behalf and surface != "agent":
        raise HTTPException(
            status_code=422,
            detail=f"{ON_BEHALF_OF_HEADER} is only valid with {SURFACE_HEADER}: agent",
        )
    return surface, on_behalf


def agent_from_request(request: Request, surface: str) -> str | None:
    """The agent's name (``claude-code``); only meaningful with the agent surface."""
    agent = (request.headers.get(AGENT_HEADER) or "").strip()[:100] or None
    if agent and surface != "agent":
        raise HTTPException(
            status_code=422,
            detail=f"{AGENT_HEADER} is only valid with {SURFACE_HEADER}: agent",
        )
    return agent


def resolve_agent_identity(approver: Approver, on_behalf_of: str | None) -> Approver:
    """Who an agent decision is attributable to (FORGE-510).

    Authenticated: the principal, and the agent must act for that same user
    (403 otherwise). Unauthenticated: the user the agent names, unverified,
    never a bare ``local:dashboard``.
    """
    if not on_behalf_of:
        raise HTTPException(
            status_code=422,
            detail=f"{ON_BEHALF_OF_HEADER} is required with {SURFACE_HEADER}: agent",
        )
    if approver.verified:
        names = {approver.actor_id, approver.actor_id.partition(":")[2], approver.display_name}
        if on_behalf_of not in names:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"an agent may only decide on behalf of the authenticated user "
                    f"('{approver.actor_id}'), not '{on_behalf_of}'"
                ),
            )
        return approver
    return Approver(actor_id=on_behalf_of, verified=False)


@router.get("", response_model=ApprovalListResponse)
async def list_approvals(
    request: Request,
    status: Literal["pending", "decided", "all"] = "pending",
    project_id: str | None = None,
    kind: ApprovalKind | None = None,
) -> ApprovalListResponse:
    """Every approval, normalized. ``status=decided`` and ``all`` are the audit views."""
    surface, _ = surface_from_request(request)
    items = [service.for_caller(i, surface) for i in await service.list_items(request.app)]
    if status == "pending":
        items = [i for i in items if i.status == "pending"]
    elif status == "decided":
        items = [i for i in items if i.status != "pending"]
    if kind is not None:
        items = [i for i in items if i.kind == kind]
    unscoped = 0
    if project_id:
        unscoped = sum(1 for i in items if i.project_id is None)
        items = [i for i in items if i.project_id == project_id]
    return ApprovalListResponse(items=items, unscoped_count=unscoped)


@router.get("/{approval_id}", response_model=ApprovalItem)
async def get_approval(approval_id: str, request: Request) -> ApprovalItem:
    surface, _ = surface_from_request(request)
    return service.for_caller(await service.get_item(request.app, approval_id), surface)


@router.post("/{approval_id}/decision", response_model=ApprovalItem)
async def decide_approval(
    approval_id: str, body: DecisionRequest, request: Request
) -> ApprovalItem:
    """Decide an approval of any kind.

    The deciding human is the authenticated principal for every kind. A
    ``reviewer`` or ``approved_by`` in the body is ignored and logged.
    """
    approver = approver_from_request(request)
    surface, on_behalf_of = surface_from_request(request)
    agent = agent_from_request(request, surface)
    if surface == "agent":
        approver = resolve_agent_identity(approver, on_behalf_of)
    ignored = [k for k in _IGNORED_BODY_IDENTITY if k in (body.model_extra or {})]
    if ignored:
        logger.warning(
            "approval_body_identity_ignored",
            approval_id=approval_id,
            fields=ignored,
            decided_by=approver.actor_id,
        )
    kind = approval_id.partition(":")[0]
    metric_kind = kind if kind in ID_PREFIXES else "unknown"
    try:
        item = await service.decide(
            request.app,
            approval_id,
            decision=body.decision,
            reason=body.reason,
            to_phase=body.to_phase,
            approver=approver,
            surface=surface,
            on_behalf_of=on_behalf_of,
            agent=agent,
        )
    except HTTPException as exc:
        outcome = "refused" if exc.status_code < 500 else "error"
        _collector().record_approval_decision(metric_kind, body.decision, surface, outcome)
        logger.info(
            "approval_decision_refused",
            approval_id=approval_id,
            decision=body.decision,
            status_code=exc.status_code,
            surface=surface,
        )
        raise
    except Exception:
        _collector().record_approval_decision(metric_kind, body.decision, surface, "error")
        raise
    _collector().record_approval_decision(metric_kind, body.decision, surface, "ok")
    logger.info(
        "approval_decided",
        approval_id=approval_id,
        kind=metric_kind,
        decision=body.decision,
        decided_by=approver.actor_id,
        approver_verified=approver.verified,
        surface=surface,
        on_behalf_of=on_behalf_of,
        agent=agent,
    )
    return item
