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
    AccountPolicy,
    ApprovalPolicy,
    account_for,
    auto_expiry,
    effective_mode,
    get_account_policy_store,
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
#: FORGE-904: the caller's own account settings.
account_router = APIRouter(prefix="/v1/account", tags=["approvals"])

Mode = Literal["off", "advise", "auto_clean", "auto"]


class DeploymentLimits(BaseModel):
    """What the people who run this MetaForge allow; set in its environment, read only here."""

    max_mode: str
    allow_human_authority: bool
    allow_release: bool
    #: Per project, per rolling hour: tool, the limit, and the variable that sets it.
    hourly_caps: list[dict[str, object]]
    #: How long a held call's approval stays usable, seconds.
    approval_ttl_seconds: float
    #: Where these are set.
    set_in: str = "the deployment's environment (METAFORGE_AUTO_APPROVE_MAX, ...)"


def deployment_limits() -> DeploymentLimits:
    import os

    from api_gateway.approvals.delegation import deployment_allows
    from api_gateway.approvals.tool_write_policy import RATE_CAPS, SPEND_CAPS, hourly_cap

    caps = [
        {"tool": tool, "per_hour": hourly_cap(tool), "set_by": env}
        for tool, (env, _) in {**SPEND_CAPS, **RATE_CAPS}.items()
    ]
    try:
        ttl = float(os.environ.get("METAFORGE_TOOL_APPROVAL_TTL_S", "") or 3600)
    except ValueError:
        ttl = 3600.0
    return DeploymentLimits(
        max_mode=max_mode(),
        allow_human_authority=deployment_allows("human_authority"),
        allow_release=deployment_allows("release"),
        hourly_caps=caps,
        approval_ttl_seconds=min(ttl, 3600.0),
    )


class ApprovalPolicyView(BaseModel):
    """The stored policy, what is in force now, and every layer above it (FORGE-904)."""

    policy: ApprovalPolicy
    effective_mode: str
    #: The highest mode this project may choose: the lower of the account's and
    #: the deployment's ceilings.
    max_mode: str
    modes: list[str]
    #: The account's own ceiling, ``None`` when it follows the deployment.
    account_max_mode: str | None = None
    #: Which layer holds the mode below what the project chose: ``expired``,
    #: ``account`` or ``deployment``; ``None`` when the project's choice is in force.
    limited_by: str | None = None
    #: May agents decide human-authority steps and releases here, and which
    #: layer decided: ``{kind: {allowed, decided_by, reason}}``.
    delegation: dict[str, dict[str, object]] = Field(default_factory=dict)
    deployment: DeploymentLimits | None = None


class ApprovalPolicyUpdate(BaseModel):
    mode: Mode
    reviewer: Literal["server", "client", "client_then_server"] = "server"
    reviewer_model: str = Field(default="", max_length=200)
    #: ``auto`` only: how long it lasts (default 24 h, at most a week).
    expires_in_hours: float | None = Field(default=None, gt=0, le=MAX_AUTO_HOURS)
    #: FORGE-904: ``None`` follows the account; ``False`` turns delegation off here.
    allow_human_authority: bool | None = None
    allow_release: bool | None = None


def _lower(a: str, b: str | None) -> str:
    return a if b is None or MODES.index(a) <= MODES.index(b) else b


async def _view(policy: ApprovalPolicy) -> ApprovalPolicyView:
    from api_gateway.approvals.delegation import delegation_view

    in_force = effective_mode(policy)
    ceiling = _lower(max_mode(), policy.account_max_mode)
    limited_by: str | None = None
    if in_force != policy.mode:
        if policy.mode == "auto" and in_force == "advise" and mode_allowed("auto"):
            limited_by = "expired"
        elif not mode_allowed(policy.mode):
            limited_by = "deployment"
        else:
            limited_by = "account"
    return ApprovalPolicyView(
        policy=policy,
        effective_mode=in_force,
        max_mode=ceiling,
        modes=list(MODES),
        account_max_mode=policy.account_max_mode,
        limited_by=limited_by,
        delegation=await delegation_view(policy.project_id),
        deployment=deployment_limits(),
    )


@router.get("/{project_id}/approval-policy", response_model=ApprovalPolicyView)
async def get_approval_policy(project_id: str) -> ApprovalPolicyView:
    """Who decides this project's gates and held writes right now, and why."""
    return await _view(await get_policy_store().get(project_id))


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
    current = await get_policy_store().get(project_id)
    account_cap = current.account_max_mode
    if account_cap is not None and MODES.index(body.mode) > MODES.index(account_cap):
        raise HTTPException(
            status_code=422,
            detail=f"mode '{body.mode}' is above your account's limit '{account_cap}' "
            "(Settings, Approvals)",
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
            allow_human_authority=body.allow_human_authority,
            allow_release=body.allow_release,
            # The project's account, so a caller with none of its own (a worker)
            # still finds the account's settings.
            account=account_for(current),
        )
        await get_policy_store().put(policy)
        policy = await get_policy_store().get(project_id)
    if body.mode != "off":
        # FORGE-743: gates already waiting are reviewed under the new policy,
        # not only gates that open from now on.
        from api_gateway.runs.gate_review import request_reviews_for_project

        try:
            await request_reviews_for_project(project_id)
        except Exception as exc:  # noqa: BLE001 - setting the policy must not fail on this
            logger.warning("approval_policy_open_gate_review_failed", error=str(exc))
    logger.info(
        "approval_policy_set",
        project_id=project_id,
        mode=body.mode,
        reviewer=body.reviewer,
        reviewer_model=policy.reviewer_model,
        expires_at=policy.expires_at,
        allow_human_authority=body.allow_human_authority,
        allow_release=body.allow_release,
        by=who,
    )
    return await _view(policy)


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


# -- the account's own settings (FORGE-904) ---------------------------------


class AccountPolicyView(BaseModel):
    """The account's settings, the deployment's limits, and what follows for its projects."""

    policy: AccountPolicy
    #: The highest mode the account's projects may choose now.
    max_mode: str
    modes: list[str]
    #: Whether agents may decide human-authority steps and releases on the
    #: account's projects (before any project turns them off).
    allow_human_authority: bool
    allow_release: bool
    deployment: DeploymentLimits


class AccountPolicyUpdate(BaseModel):
    #: ``None`` follows the deployment's ceiling.
    max_mode: Mode | None = None
    #: ``None`` follows the deployment; ``False`` turns delegation off for every project.
    allow_human_authority: bool | None = None
    allow_release: bool | None = None


def _account_or_403() -> str:
    account = account_for(None)
    if account is None:
        raise HTTPException(status_code=403, detail="This sign-in has no account.")
    return account


def _account_view(policy: AccountPolicy) -> AccountPolicyView:
    limits = deployment_limits()
    return AccountPolicyView(
        policy=policy,
        max_mode=_lower(limits.max_mode, policy.max_mode),
        modes=list(MODES),
        allow_human_authority=limits.allow_human_authority
        and policy.allow_human_authority is not False,
        allow_release=limits.allow_release and policy.allow_release is not False,
        deployment=limits,
    )


@account_router.get("/approval-policy", response_model=AccountPolicyView)
async def get_account_approval_policy() -> AccountPolicyView:
    """Your account's approval settings, and the deployment's limits above them."""
    return _account_view(await get_account_policy_store().get(_account_or_403()))


@account_router.put("/approval-policy", response_model=AccountPolicyView)
async def put_account_approval_policy(
    body: AccountPolicyUpdate, request: Request
) -> AccountPolicyView:
    """Set your account's approval settings. A person's decision only.

    The ceiling cannot be above the deployment's; turning delegation on where the
    deployment has it off is refused, so the person sees why.
    """
    from api_gateway.auth.dependencies import current_principal

    surface, _ = surface_from_request(request)
    if surface == "agent":
        raise HTTPException(
            status_code=403,
            detail="changing approval settings is a person's decision; an agent cannot set them",
        )
    account = _account_or_403()
    limits = deployment_limits()
    if body.max_mode is not None and not mode_allowed(body.max_mode):
        raise HTTPException(
            status_code=422,
            detail=f"'{body.max_mode}' is above this deployment's ceiling '{limits.max_mode}'",
        )
    for field, allowed in (
        ("allow_human_authority", limits.allow_human_authority),
        ("allow_release", limits.allow_release),
    ):
        if getattr(body, field) is True and not allowed:
            raise HTTPException(
                status_code=422,
                detail=f"{field} is off for this whole deployment; an account cannot turn it on",
            )
    principal = current_principal(request)
    who = f"user:{principal.subject}" if principal is not None else f"local:{surface}"
    with tracer.start_as_current_span("account_policy.put") as span:
        span.set_attribute("policy.max_mode", str(body.max_mode))
        policy = AccountPolicy(
            account=account,
            max_mode=body.max_mode,
            allow_human_authority=body.allow_human_authority,
            allow_release=body.allow_release,
            updated_by=who,
            updated_at=datetime.now(UTC).isoformat(),
        )
        await get_account_policy_store().put(policy)
    logger.info(
        "account_approval_policy_set",
        max_mode=body.max_mode,
        allow_human_authority=body.allow_human_authority,
        allow_release=body.allow_release,
        by=who,
    )
    return _account_view(policy)
