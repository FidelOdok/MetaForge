"""Item migration API: fold a project's legacy nodes into items (FORGE-529).

``POST /v1/twin/projects/{project_id}/item-migration/plan`` is the dry run:
it returns the plan (JSON) and the same plan as a readable table, and writes
nothing. ``POST .../item-migration/apply`` applies a plan the caller passes
back, and only with an explicit approval (``approve: true`` and a reason),
attributed to the human the request comes from like every other approval.
The plan's hash is checked, and a plan made against a twin that has changed
since is refused with 409.

The migration itself is :mod:`twin_core.items.migration`; this module only
wires in what the twin cannot know: how each design-flow run's gate ended and
which slots its flow declared (the run store and flow versions), and which
nodes the project's membership table links to it.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from api_gateway.auth.approver import approver_from_request
from observability.tracing import get_tracer
from twin_core.items.migration import (
    ItemMigration,
    MigrationPlan,
    MigrationResult,
    PlanTamperedError,
    RunInfo,
    StalePlanError,
    render_report,
)

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.item_migration")

router = APIRouter(prefix="/v1/twin", tags=["twin"])

#: Run status -> what the migration makes of its nodes.
_RUN_OUTCOME = {
    "completed": "approved",
    "rejected": "rejected",
    "failed": "rejected",
    "canceled": "rejected",
    "timed_out": "rejected",
    "queued": "open",
    "running": "open",
    "awaiting_approval": "open",
}


class MigrationPlanResponse(BaseModel):
    plan: MigrationPlan
    #: The plan as a readable table, for review.
    report: str
    empty: bool


class MigrationApplyRequest(BaseModel):
    plan: MigrationPlan = Field(description="The plan exactly as the dry run returned it.")
    approve: bool = Field(
        default=False, description="Must be true: applying the plan writes to the twin."
    )
    reason: str = Field(default="", description="Why this plan is approved (kept in the log).")


class MigrationApplyResponse(BaseModel):
    result: MigrationResult
    approved_by: str
    approver_verified: bool


def _twin() -> Any:
    from api_gateway.twin.routes import get_twin

    return get_twin()


def _slots_for(run: Any) -> dict[str, list[dict[str, str]]]:
    """The deliverable slots each phase of ``run``'s flow declares (FORGE-524)."""
    from orchestrator.design_flow.slots import effective_slots
    from orchestrator.design_flow.spec import definition_from_frozen, get_flow

    request = run.request or {}
    version_id = request.get("flow_version_id")
    if version_id:
        from orchestrator.design_flow.versions import get_version_store

        definition = definition_from_frozen(get_version_store().get(str(version_id)).frozen)
    else:
        definition = get_flow(request.get("flow") or None)
    return {
        phase.id: [
            {"item_type": s.item_type, "name": s.name, "item_key": s.item_key}
            for s in effective_slots(phase)
            if s.item_key
        ]
        for phase in definition.phases
    }


async def run_info(run_id: str) -> RunInfo | None:
    """How run ``run_id`` ended, from the run store; ``None`` when it is unknown."""
    from api_gateway.runs.routes import get_run_store

    try:
        run = get_run_store().get(run_id)
    except KeyError:
        return None
    status = str(getattr(run.status, "value", run.status))
    outcome = _RUN_OUTCOME.get(status, "unknown")
    reason = None
    if outcome == "rejected":
        reason = run.approval_reason or run.error or f"the run ended {status}"
    try:
        slots = _slots_for(run)
    except Exception as exc:  # noqa: BLE001 - no slots just means no flow_slot rule
        logger.debug("item_migration_slots_unavailable", run_id=run_id, error=str(exc))
        slots = {}
    return RunInfo(run_id=run_id, outcome=outcome, reason=reason, slots=slots)


async def project_members(project_id: UUID) -> list[UUID]:
    """Node ids the project's membership table links to it (the other half of scope)."""
    from api_gateway.projects.routes import get_project_backend

    project = await get_project_backend().get_project(str(project_id))
    out: list[UUID] = []
    for wp in getattr(project, "work_products", None) or []:
        try:
            out.append(UUID(str(wp.id)))
        except ValueError:
            continue
    return out


def _migration() -> ItemMigration:
    return ItemMigration(_twin(), run_lookup=run_info, member_ids=project_members)


def _project(project_id: str) -> UUID:
    try:
        return UUID(project_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="project_id must be a UUID") from exc


@router.post("/projects/{project_id}/item-migration/plan", response_model=MigrationPlanResponse)
async def plan_item_migration(project_id: str) -> MigrationPlanResponse:
    """Dry run: how the project's legacy nodes would become items. Writes nothing."""
    pid = _project(project_id)
    with tracer.start_as_current_span("twin.item_migration.plan_route") as span:
        span.set_attribute("project.id", str(pid))
        plan = await _migration().plan(pid)
        return MigrationPlanResponse(plan=plan, report=render_report(plan), empty=plan.empty)


@router.post("/projects/{project_id}/item-migration/apply", response_model=MigrationApplyResponse)
async def apply_item_migration(
    project_id: str, body: MigrationApplyRequest, request: Request
) -> MigrationApplyResponse:
    """Apply a reviewed plan. Requires ``approve: true``; refused when the twin changed."""
    pid = _project(project_id)
    if body.plan.project_id != str(pid):
        raise HTTPException(status_code=400, detail="the plan is for a different project")
    if not body.approve:
        raise HTTPException(
            status_code=403,
            detail=(
                "applying a migration writes to the twin and needs an explicit approval: "
                "review the dry run, then send it back with approve: true and a reason"
            ),
        )
    approver = approver_from_request(request)
    with tracer.start_as_current_span("twin.item_migration.apply_route") as span:
        span.set_attribute("project.id", str(pid))
        span.set_attribute("migration.approver_verified", approver.verified)
        try:
            result = await _migration().apply(body.plan, applied_by=approver.actor_id)
        except StalePlanError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except PlanTamperedError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        logger.info(
            "item_migration_approved",
            project_id=str(pid),
            plan_hash=body.plan.plan_hash,
            approved_by=approver.actor_id,
            approver_verified=approver.verified,
            reason=body.reason[:500] or None,
        )
        return MigrationApplyResponse(
            result=result, approved_by=approver.actor_id, approver_verified=approver.verified
        )
