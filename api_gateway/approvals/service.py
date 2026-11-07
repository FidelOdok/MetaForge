"""Collect, look up and decide approvals across every kind (FORGE-507).

Decisions are not re-implemented here: each kind delegates to the handler
function the old route already calls (``decide_run_gate``,
``decide_tool_approval``, ``decide_change``, ``approve_loop``,
``approve_sketch_node``, ``approve_drawing_node``), so 404/409/422 rules stay
identical on both surfaces.
"""

from __future__ import annotations

import asyncio
import re
import time
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from fastapi import HTTPException

from api_gateway.approvals.findings import findings_from_lines, findings_from_reason
from api_gateway.approvals.schemas import (
    ID_PREFIXES,
    REASON_REQUIRED_FOR,
    ApprovalItem,
    DecisionRecord,
    GateFinding,
    GateReview,
    Surface,
)
from api_gateway.assistant import routes as assistant_routes
from api_gateway.assistant.schemas import ApprovalDecisionType, ChangeStatus, DesignChangeProposal
from api_gateway.chat import tool_approvals
from api_gateway.design_loop import routes as design_loop_routes
from api_gateway.runs import routes as run_routes
from api_gateway.runs.schemas import project_of
from api_gateway.twin import routes as twin_routes
from api_gateway.uploads import routes as upload_routes
from api_gateway.uploads import service as upload_service
from api_gateway.uploads.store import StagedUpload, UploadNotFoundError
from mcp_core.guardrails import Approver, requires_human_authority
from orchestrator.harness.runs import (
    UNANSWERED,
    ApprovalDecision,
    Run,
    RunNotFoundError,
    RunStatus,
)
from twin_core.models.enums import NodeType, WorkProductType

logger = structlog.get_logger(__name__)

#: Decisions that apply to each kind outside a gate's own readiness rules.


def _iso(value: Any) -> str | None:
    """ISO-8601 UTC from a ``datetime`` or an epoch-seconds float."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=UTC)).isoformat()
    return datetime.fromtimestamp(float(value), tz=UTC).isoformat()


_SURFACES = ("dashboard", "cli", "agent", "chat", "reviewer")


def _surface(value: Any) -> Surface:
    return value if value in _SURFACES else "unknown"


def _record(item_id: str, fallback: DecisionRecord | None) -> DecisionRecord | None:
    """The decision as persisted on the underlying record (FORGE-507)."""
    return fallback


_RUN_STATUS = {
    RunStatus.AWAITING_APPROVAL: "pending",
    RunStatus.TIMED_OUT: "expired",
    RunStatus.CANCELED: "canceled",
    RunStatus.REJECTED: "rejected",
}


def _run_status(run: Run, audited: DecisionRecord | None) -> str:
    """Contract status of a run-backed approval."""
    # FORGE-639: a hold a person decided was, until the fix, left RUNNING and
    # then cancelled by the heartbeat reaper as abandoned. The decision logged
    # on it is what happened; the reaper's "canceled" is not.
    if run.status in (RunStatus.CANCELED, RunStatus.FAILED) and audited is not None:
        if audited.decision == "approve":
            return "approved"
        if audited.decision == "reject":
            return "rejected"
    if run.status in _RUN_STATUS:
        return _RUN_STATUS[run.status]
    if audited is not None and audited.decision == "retry":
        return "retried"
    if audited is not None and audited.decision == "rework":
        return "reworked"
    return "approved"


#: Where a run-backed approval keeps its decision log. ``Run.request`` is part
#: of what the run ledger persists on every transition, so the surface and
#: on-behalf-of survive a restart with the run (FORGE-507).
DECISIONS_KEY = "decisions"


def _run_fallback(run: Run) -> DecisionRecord | None:
    logged = run.request.get(DECISIONS_KEY)
    if isinstance(logged, list) and logged:
        return DecisionRecord(**logged[-1])
    if not run.approved_by:
        return None
    return DecisionRecord(
        decision="reject" if run.status is RunStatus.REJECTED else "approve",
        approver=run.approved_by,
        approver_verified=run.approver_verified,
        decided_at=_iso(run.updated_at),
    )


def _decidable(
    pending: bool, allowed: list[str], not_decidable: str | None = None
) -> tuple[bool, str | None]:
    if not pending:
        return False, "this approval is already closed"
    if not_decidable:
        return False, not_decidable
    return bool(allowed), None if allowed else "no decision is available for this approval"


# ── gates ────────────────────────────────────────────────────────────────


def _gate_title(run: Run, snap: dict[str, Any] | None) -> str:
    flow = run.request.get("flow") or run.request.get("kind") or "run"
    phase = (snap or {}).get("phase")
    return f"{flow} gate" + (f" at phase '{phase}'" if phase else "")


async def gate_item(run: Run, *, snap: dict[str, Any] | None = None) -> ApprovalItem:
    item_id = f"gate:{run.id}"
    pending = run.status is RunStatus.AWAITING_APPROVAL
    design_flow = run_routes.is_design_flow_run(run)
    findings: list[GateFinding] = []
    allowed: list[str] = []
    targets: list[str] = []
    missing = bool((snap or {}).get("workflow_missing"))
    if pending and missing:
        # FORGE-516: the workflow is gone, so nothing can resume the run.
        allowed = ["reject"]
    elif pending:
        lines = [str(f) for f in (snap or {}).get("findings") or []]
        findings = (
            findings_from_lines(lines) if lines else findings_from_reason(run.approval_reason)
        )
        ready = bool(snap.get("ready", True)) if snap is not None else not findings
        if ready:
            allowed.append("approve")
        allowed.append("reject")
        if design_flow:
            retries_left = (snap or {}).get("retries_left")
            if not (isinstance(retries_left, int) and retries_left <= 0):
                allowed.append("retry")
            reworks_left = (snap or {}).get("reworks_left")
            phase_ids = run_routes.run_phase_ids(run)
            current = (snap or {}).get("phase")
            targets = (
                phase_ids[: phase_ids.index(str(current))]
                if current and str(current) in phase_ids
                else phase_ids
            )
            if targets and not (isinstance(reworks_left, int) and reworks_left <= 0):
                allowed.append("rework")
            else:
                targets = []
    record = _record(item_id, _run_fallback(run))
    decidable, why = _decidable(pending, allowed)
    if pending and missing:
        why = (
            "the run's workflow no longer exists; approve, retry and rework cannot "
            "continue it, only reject is available"
        )
    return ApprovalItem(
        id=item_id,
        kind="gate",
        status=_run_status(run, record) if not pending else "pending",  # type: ignore[arg-type]
        title=_gate_title(run, snap),
        summary=(run.approval_reason or "")[:300],
        project_id=project_of(run.request),
        created_at=_iso(run.created_at),
        deadline=_iso(run.approval_deadline),
        route="dashboard",
        requested_by=str(run.request.get("caller") or run.request.get("requested_by") or "")
        or None,
        reason_held=run.approval_reason,
        findings=findings,
        allowed_decisions=allowed,  # type: ignore[arg-type]
        rework_targets=targets,
        decidable=decidable,
        not_decidable_reason=why,
        detail={
            "run_id": run.id,
            "phase": (snap or {}).get("phase"),
            "gate": _gate_name(run.approval_reason),
            "attempt": (snap or {}).get("attempt"),
            "retries_left": (snap or {}).get("retries_left"),
            "rework_cycles_left": (snap or {}).get("reworks_left"),
        },
        decision=None if pending else record,
        review=current_review(run, snap) if pending else None,
        # FORGE-788 (RT-030): what the person reviews here (the gate's phase,
        # attempt and the run's drafts). A decision naming an older one is 409.
        base_revision=await _gate_revision(run, snap) if pending else None,
    )


async def _gate_revision(run: Run, snap: dict[str, Any] | None) -> str | None:
    from api_gateway.runs import change_sets as run_change_sets

    try:
        return await run_change_sets.gate_revision(run, snap)
    except Exception as exc:  # noqa: BLE001 - a gate stays decidable without one
        logger.info("approval_gate_revision_unavailable", run_id=run.id, error=str(exc))
        return None


_GATE_NAME = re.compile(r"^(?:Gate '([^']+)'|\[([^\]]+)\])")


def _gate_name(reason: str | None) -> str | None:
    match = _GATE_NAME.match(reason or "")
    return (match.group(1) or match.group(2)) if match else None


def _is_gate_run(run: Run) -> bool:
    return run.status is RunStatus.AWAITING_APPROVAL or run.approved_by is not None


# ── held tool calls ──────────────────────────────────────────────────────


def _tool_kind(req: dict[str, Any]) -> str:
    marker = req.get("kind")
    if marker == "design_flow_proposal":
        return "flow_proposal"
    if marker == "design_flow_version":
        return "flow_version"
    if marker == "design_flow_patch":
        return "flow_patch"
    tool = str(req.get("tool") or "")
    if requires_human_authority(tool) or requires_human_authority(tool.replace(".", "_")):
        return "human_authority"
    return "tool_call"


def tool_item(run: Run) -> ApprovalItem:
    item_id = f"tool:{run.id}"
    pending = run.status is RunStatus.AWAITING_APPROVAL
    req = run.request
    route = req.get("route")
    kind = _tool_kind(req)
    # FORGE-780: decidable whichever route asked. An inline hold used to be
    # "answered in the client's own approval prompt" only, and once the
    # client had timed the call out there was nowhere left to answer it.
    allowed = ["approve", "reject"] if pending else []
    not_decidable: str | None = None
    if kind == "flow_patch":
        # FORGE-539: what a patch re-runs and what it keeps is the decision.
        detail = {
            "flow_version_id": req.get("flow_version_id"),
            "run_id": req.get("run_id"),
            "reason": req.get("reason"),
            "rerun": req.get("rerun") or [],
            "preserved": req.get("preserved") or [],
            "changes": req.get("changes") or [],
        }
        title = f"Patch to run {req.get('run_id') or ''}".strip()
    elif kind in ("flow_proposal", "flow_version"):
        detail: dict[str, Any] = {
            "flow_version_id": req.get("flow_version_id"),
            "intent": req.get("intent") or req.get("goal") or req.get("flow"),
            "changes": req.get("changes") or req.get("diff") or [],
        }
        title = "Flow version for approval" if kind == "flow_version" else "Flow proposal"
    else:
        detail = {"tool": req.get("tool"), "arguments": req.get("arguments") or {}}
        if req.get("arguments_hash"):
            # FORGE-780: what an approval covers, and whether it was used.
            detail["arguments_hash"] = req.get("arguments_hash")
            detail["used"] = bool(req.get("consumed_at"))
        title = f"Tool call: {req.get('tool') or 'unknown tool'}"
    record = _record(item_id, _run_fallback(run))
    decidable, why = _decidable(pending, allowed, not_decidable)
    return ApprovalItem(
        id=item_id,
        kind=kind,  # type: ignore[arg-type]
        status=_run_status(run, record),  # type: ignore[arg-type]
        title=title,
        summary=(run.approval_reason or run.error or "")[:300],
        project_id=project_of(req) or (str(req["project"]) if req.get("project") else None),
        created_at=_iso(run.created_at),
        deadline=_iso(run.approval_deadline),
        route=route if route in ("dashboard", "elicitation") else None,
        requested_by=str(req.get("caller") or "") or None,
        reason_held=run.approval_reason or run.error,
        allowed_decisions=allowed,  # type: ignore[arg-type]
        decidable=decidable,
        not_decidable_reason=why,
        detail=detail,
        decision=record,
    )


# ── assistant change proposals ───────────────────────────────────────────


async def _change_revision(app: Any, p: DesignChangeProposal) -> str | None:
    """The revision of the design a pending change proposal was made against (RT-030).

    A digest of the content of every work product the proposal affects, read
    from the twin now. When any of them changes, the digest does, and a
    decision sent with the one the person reviewed is refused (FORGE-788).
    ``None`` when the proposal names none, is decided, or there is no twin.
    """
    import hashlib

    if p.status is not ChangeStatus.PENDING or not p.work_products_affected:
        return None
    twin = _twin_of(app)
    if twin is None:
        return None
    parts: list[str] = []
    try:
        for wp_id in sorted(str(w) for w in p.work_products_affected):
            wp = await twin.get_work_product(UUID(wp_id))
            if wp is None:
                parts.append(f"{wp_id}:missing")
                continue
            marker = getattr(wp, "content_hash", None) or _iso(getattr(wp, "updated_at", None))
            parts.append(f"{wp_id}:{marker or ''}")
    except Exception as exc:  # noqa: BLE001 - the proposal stays decidable without one
        logger.info(
            "approval_change_revision_unavailable", change_id=str(p.change_id), error=str(exc)
        )
        return None
    return "wp:" + hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


def change_item(p: DesignChangeProposal, *, base_revision: str | None = None) -> ApprovalItem:
    item_id = f"change:{p.change_id}"
    pending = p.status is ChangeStatus.PENDING
    fallback = (
        DecisionRecord(
            decision="approve" if p.status is not ChangeStatus.REJECTED else "reject",
            reason=p.decision_reason or "",
            approver=p.reviewer,
            approver_verified=p.reviewer_verified,
            surface=_surface(p.decision_surface),
            on_behalf_of=p.decision_on_behalf_of,
            agent=p.decision_agent,
            decided_at=_iso(p.decided_at),
        )
        if p.reviewer
        else None
    )
    allowed = ["approve", "reject"] if pending else []
    decidable, why = _decidable(pending, allowed)
    return ApprovalItem(
        id=item_id,
        kind="design_change",
        status="pending"
        if pending
        else ("rejected" if p.status is ChangeStatus.REJECTED else "approved"),
        title=f"Design change by {p.agent_code}",
        summary=p.description[:300],
        project_id=p.project_id,
        created_at=_iso(p.created_at),
        route="dashboard",
        requested_by=p.agent_code,
        reason_held=p.description,
        allowed_decisions=allowed,  # type: ignore[arg-type]
        decidable=decidable,
        not_decidable_reason=why,
        detail={
            "change_id": str(p.change_id),
            "diff": p.diff,
            "affected": [str(w) for w in p.work_products_affected],
        },
        decision=_record(item_id, fallback),
        base_revision=base_revision,
    )


# ── twin-backed kinds ────────────────────────────────────────────────────


def _twin_of(app: Any) -> Any | None:
    return getattr(app.state, "twin", None)


def _loop_item(winner: Any) -> ApprovalItem:
    item_id = f"design_loop:{winner.loop_id}"
    pending = not winner.approved
    fallback = (
        DecisionRecord(
            decision="approve",
            approver=winner.approved_by,
            approver_verified=winner.approver_verified,
            surface=_surface(winner.approval_surface),
            on_behalf_of=winner.approval_on_behalf_of,
            agent=winner.approval_agent,
            decided_at=_iso(winner.approved_at),
        )
        if winner.approved
        else None
    )
    allowed = ["approve"] if pending else []
    decidable, why = _decidable(pending, allowed)
    return ApprovalItem(
        id=item_id,
        kind="design_loop",
        status="pending" if pending else "approved",
        title=f"Design loop winner: {winner.parameter_name}={winner.parameter_value:g}",
        summary=(
            f"{winner.metric}={winner.objective_value:g} at iteration {winner.iteration_number}"
        ),
        project_id=str(winner.project_id) if winner.project_id else None,
        created_at=_iso(winner.created_at),
        route="dashboard",
        allowed_decisions=allowed,  # type: ignore[arg-type]
        decidable=decidable,
        not_decidable_reason=why,
        detail={
            "loop_id": str(winner.loop_id),
            "work_product_id": str(winner.work_product_id),
            "parameter_name": winner.parameter_name,
            "parameter_value": winner.parameter_value,
            "metric": winner.metric,
            "objective_value": winner.objective_value,
            "constraints_status": winner.constraints_status,
            "feasible": winner.feasible,
        },
        decision=_record(item_id, fallback),
    )


_WP_LABELS = {
    "sketch": "Design sketch",
    "drawing": "Technical drawing",
    "parametric_sketch": "Parametric sketch",
}
_WP_SUMMARIES = {
    "sketch": "{label} awaiting sign-off before build work",
    "drawing": "{label} awaiting sign-off before build work",
    "parametric_sketch": (
        "{label} awaiting sign-off: its accepted rules and confirmed dimensions become "
        "the editable sketch"
    ),
}
_WP_TYPES = {
    "sketch": WorkProductType.DESIGN_SKETCH,
    "drawing": WorkProductType.TECHNICAL_DRAWING,
    "parametric_sketch": WorkProductType.PARAMETRIC_SKETCH,
}


def _wp_item(kind: str, wp: Any) -> ApprovalItem:
    item_id = f"{kind}:{wp.id}"
    approved = bool(wp.metadata.get("approved"))
    label = _WP_LABELS[kind]
    fallback = (
        DecisionRecord(
            decision="approve",
            approver=wp.metadata.get("approved_by"),
            approver_verified=bool(wp.metadata.get("approver_verified")),
            surface=_surface(wp.metadata.get("approval_surface")),
            on_behalf_of=wp.metadata.get("approval_on_behalf_of"),
            agent=wp.metadata.get("approval_agent"),
            decided_at=wp.metadata.get("approved_at"),
        )
        if approved
        else None
    )
    allowed = [] if approved else ["approve"]
    decidable, why = _decidable(not approved, allowed)
    return ApprovalItem(
        id=item_id,
        kind=kind,  # type: ignore[arg-type]
        status="approved" if approved else "pending",
        title=f"{label}: {wp.name}",
        summary="" if approved else _WP_SUMMARIES[kind].format(label=label),
        project_id=str(wp.project_id) if wp.project_id else None,
        created_at=_iso(wp.created_at),
        route="dashboard",
        requested_by=wp.created_by,
        allowed_decisions=allowed,  # type: ignore[arg-type]
        decidable=decidable,
        not_decidable_reason=why,
        detail={"node_id": str(wp.id), "name": wp.name, "file_path": wp.file_path},
        decision=_record(item_id, fallback),
        # RT-030: the content the person reviewed. A changed file is a new hash.
        base_revision=str(wp.content_hash) if wp.content_hash else None,
    )


async def _twin_items(app: Any) -> list[ApprovalItem]:
    twin = _twin_of(app)
    if twin is None:
        return []
    items: list[ApprovalItem] = []
    for kind, wp_type in _WP_TYPES.items():
        try:
            for wp in await twin.list_work_products(work_product_type=wp_type):
                items.append(_wp_item(kind, wp))
        except Exception as exc:  # noqa: BLE001 - one unreadable kind must not hide the others
            logger.warning("approvals_list_failed", kind=kind, error=str(exc))
    try:
        winners = await twin._graph.list_nodes(  # noqa: SLF001
            node_type=NodeType.DESIGN_LOOP_ITERATION, filters={"is_winner": True}
        )
        items.extend(_loop_item(w) for w in winners)
    except Exception as exc:  # noqa: BLE001
        logger.warning("approvals_list_failed", kind="design_loop", error=str(exc))
    return items


_RERUN_STATUS = {"proposed": "pending", "approved": "approved", "rejected": "rejected"}


def rerun_item(impact: Any, rerun: Any) -> ApprovalItem:
    """A re-check a cross-domain change proposed, waiting for a person (FORGE-793)."""
    from api_gateway.twin.impact_service import rerun_approval_id

    item_id = rerun_approval_id(str(impact.id), rerun.id)
    status = _RERUN_STATUS.get(rerun.status, "pending")
    allowed = ["approve", "reject"] if status == "pending" else []
    decidable, why = _decidable(status == "pending", allowed)
    fallback = (
        DecisionRecord(
            decision="approve" if rerun.status == "approved" else "reject",
            reason=rerun.decision_reason or "",
            approver=rerun.decided_by,
            decided_at=rerun.decided_at,
        )
        if rerun.decided_by
        else None
    )
    stale = next((s for s in impact.stale_results if s.record_id == rerun.record_id), None)
    return ApprovalItem(
        id=item_id,
        kind="rerun",
        status=status,  # type: ignore[arg-type]
        title=rerun.label,
        summary=f"{impact.summary} {rerun.reason}".strip(),
        project_id=impact.project_id,
        created_at=impact.created_at,
        route="dashboard",
        requested_by="metaforge:impact",
        reason_held=rerun.disposition_reason or None,
        allowed_decisions=allowed,  # type: ignore[arg-type]
        decidable=decidable,
        not_decidable_reason=why,
        detail={
            "impact_id": impact.id,
            "operation_id": impact.operation_id,
            "tool": rerun.tool,
            "target_ref": rerun.target_ref,
            "check_kind": rerun.check_kind,
            "consequential": rerun.consequential,
            "change": f"{impact.change.old_ref} -> {impact.change.new_ref}",
            "stale_result": stale.model_dump(mode="json") if stale else None,
        },
        decision=_record(item_id, fallback),
        # RT-030: the change the person reviewed. A later change is a new impact.
        base_revision=impact.change.new_ref,
    )


async def _rerun_items(app: Any) -> list[ApprovalItem]:
    """Every rerun that waited, or waits, for a person."""
    from api_gateway.twin.impact_service import all_impacts

    twin = _twin_of(app)
    if twin is None:
        return []
    try:
        impacts = await all_impacts(twin)
    except Exception as exc:  # noqa: BLE001 - one unreadable kind must not hide the others
        logger.warning("approvals_list_failed", kind="rerun", error=str(exc))
        return []
    return [
        rerun_item(impact, rerun)
        for impact in impacts
        for rerun in impact.proposed_reruns
        if rerun.disposition == "requires_approval"
    ]


def upload_item(upload: StagedUpload) -> ApprovalItem:
    """A staged upload as an approval: approve imports its files, reject drops them."""
    item_id = f"upload:{upload.id}"
    status = {
        "open": "pending",
        "pending": "pending",
        "approved": "approved",
        "rejected": "rejected",
        "expired": "expired",
    }[upload.status]
    accepted = upload.accepted
    left_out = [f for f in upload.files if f.error is not None and not f.work_product_id]
    names = ", ".join(f.name for f in accepted[:3]) + (
        f" and {len(accepted) - 3} more" if len(accepted) > 3 else ""
    )
    allowed = ["approve", "reject"] if upload.status == "pending" else []
    decidable, why = _decidable(upload.status == "pending", allowed)
    if upload.status == "open":
        why = "still receiving files; it can be decided once the upload is finished"
    record = (
        DecisionRecord(
            decision=upload.decision.decision,
            reason=upload.decision.reason,
            approver=upload.decision.approver,
            approver_verified=upload.decision.approver_verified,
            surface=_surface(upload.decision.surface),
            decided_at=upload.decision.decided_at,
        )
        if upload.decision
        else None
    )
    return ApprovalItem(
        id=item_id,
        kind="upload",
        status=status,  # type: ignore[arg-type]
        title=f"Upload: {names or 'no importable files'}",
        summary=(
            f"{len(accepted)} file(s), {upload.total_size} bytes, from {upload.source}"
            + (f"; {len(left_out)} cannot be imported" if left_out else "")
            + (f". {upload.description}" if upload.description else "")
        ),
        project_id=upload.project_id,
        created_at=upload.created_at,
        route="dashboard",
        requested_by=upload.agent or upload.requested_by,
        allowed_decisions=allowed,  # type: ignore[arg-type]
        decidable=decidable,
        not_decidable_reason=why,
        detail={
            "upload_id": upload.id,
            "source": upload.source,
            "files": [
                {
                    **f.model_dump(),
                    "preview_url": (
                        f"/v1/uploads/{upload.id}/files/{f.name}"
                        if f.error is None and upload.status == "pending"
                        else None
                    ),
                }
                for f in upload.files
            ],
        },
        decision=_record(item_id, record),
    )


def _upload_items() -> list[ApprovalItem]:
    try:
        store = upload_routes.get_upload_store()
        upload_service.expire_overdue(store)
        return [upload_item(u) for u in store.list()]
    except Exception as exc:  # noqa: BLE001 - one unreadable kind must not hide the others
        logger.warning("approvals_list_failed", kind="upload", error=str(exc))
        return []


async def _decide_upload(
    app: Any, upload_id: str, decision: str, reason: str, approver: Approver, surface: str
) -> None:
    store = upload_routes.get_upload_store()
    upload = store.get(upload_id)
    try:
        if decision == "approve":
            twin = _twin_of(app) or twin_routes.get_twin()
            await upload_service.approve(
                store,
                twin,
                upload,
                approver=approver.label,
                approver_verified=approver.verified,
                surface=surface,
            )
        else:
            upload_service.reject(
                store,
                upload,
                reason=reason,
                approver=approver.label,
                approver_verified=approver.verified,
                surface=surface,
            )
    except upload_service.UploadError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


# ── list / get ───────────────────────────────────────────────────────────


#: Kinds only a person may decide, unless the owner delegates them (FORGE-510).
#: An upload is one: the agent that sent the files may not let them in itself.
HUMAN_AUTHORITY_KINDS: tuple[str, ...] = (
    "human_authority",
    "design_loop",
    "upload",
    # A parametric sketch is the sketch a person stands behind (PR-004).
    "parametric_sketch",
    # A re-check a cross-domain change calls for waits for a person (XDM-007).
    "rerun",
)
ALLOW_AGENT_HUMAN_AUTHORITY_ENV = "METAFORGE_ALLOW_AGENT_HUMAN_AUTHORITY"
AGENT_HUMAN_AUTHORITY_REASON = (
    "this approval needs a human decision; agents may not decide it unless the gateway "
    f"owner sets {ALLOW_AGENT_HUMAN_AUTHORITY_ENV}=true"
)


def agent_may_decide_human_authority() -> bool:
    # One reading of the owner's switch, shared with the MCP server (FORGE-827).
    from mcp_core.guardrails import human_authority_delegated

    return human_authority_delegated()


def for_caller(item: ApprovalItem, surface: str) -> ApprovalItem:
    """The item as an agent caller may act on it: human-authority kinds are not decidable."""
    if (
        surface != "agent"
        or item.kind not in HUMAN_AUTHORITY_KINDS
        or agent_may_decide_human_authority()
    ):
        return item
    return item.model_copy(
        update={
            "allowed_decisions": [],
            "decidable": False,
            "not_decidable_reason": (
                item.not_decidable_reason
                if item.status != "pending" and item.not_decidable_reason
                else AGENT_HUMAN_AUTHORITY_REASON
            ),
        }
    )


async def list_items(app: Any) -> list[ApprovalItem]:
    """Every approval of every kind, newest first."""
    tool_approvals.expire_overdue_holds()
    runs = [r for r in run_routes.get_run_store().list() if _is_gate_run(r)]
    snaps = await asyncio.gather(
        *(
            run_routes.gate_snapshot(r) if r.status is RunStatus.AWAITING_APPROVAL else _none()
            for r in runs
        )
    )
    items = [await gate_item(r, snap=sn) for r, sn in zip(runs, snaps, strict=True)]
    for r in tool_approvals.get_approval_store().list():
        if r.status is RunStatus.AWAITING_APPROVAL or r.approved_by or r.status in UNANSWERED:
            items.append(tool_item(r))
    for p in assistant_routes.workflow.list_proposals():
        items.append(change_item(p, base_revision=await _change_revision(app, p)))
    items.extend(await _twin_items(app))
    items.extend(await _rerun_items(app))
    items.extend(_upload_items())
    items.sort(key=lambda i: i.created_at or "", reverse=True)
    return items


async def _none() -> None:
    return None


def _not_found(item_id: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"approval '{item_id}' not found")


def split_id(item_id: str) -> tuple[str, str]:
    kind, sep, raw = item_id.partition(":")
    if not sep or not raw or kind not in ID_PREFIXES:
        raise _not_found(item_id)
    return kind, raw


async def get_item(app: Any, item_id: str) -> ApprovalItem:
    kind, raw = split_id(item_id)
    if kind == "gate":
        try:
            run = run_routes.get_run_store().get(raw)
        except RunNotFoundError as exc:
            raise _not_found(item_id) from exc
        if run.status is RunStatus.AWAITING_APPROVAL:
            run = await run_routes._reconcile_run(run)  # noqa: SLF001
        snap = (
            await run_routes.gate_snapshot(run)
            if run.status is RunStatus.AWAITING_APPROVAL
            else None
        )
        if not _is_gate_run(run):
            raise _not_found(item_id)
        return await gate_item(run, snap=snap)
    if kind == "tool":
        tool_approvals.expire_overdue_holds()
        try:
            return tool_item(tool_approvals.get_approval_store().get(raw))
        except RunNotFoundError as exc:
            raise _not_found(item_id) from exc
    if kind == "change":
        proposal = _uuid(raw, item_id) and assistant_routes.workflow.get_proposal(UUID(raw))
        if not proposal:
            raise _not_found(item_id)
        return change_item(proposal, base_revision=await _change_revision(app, proposal))
    if kind == "upload":
        try:
            return upload_item(upload_routes.get_upload_store().get(raw))
        except UploadNotFoundError as exc:
            raise _not_found(item_id) from exc
    twin = _twin_of(app)
    if twin is None:
        raise _not_found(item_id)
    if kind == "rerun":
        from api_gateway.twin.impact_service import RerunNotFound, get_rerun

        try:
            impact, rerun = await get_rerun(twin, raw)
        except RerunNotFound as exc:
            raise _not_found(item_id) from exc
        if rerun.disposition != "requires_approval":
            raise _not_found(item_id)
        return rerun_item(impact, rerun)
    _uuid(raw, item_id)
    if kind == "design_loop":
        iterations = await twin.list_design_loop_iterations(UUID(raw))
        winner = next((i for i in iterations if i.is_winner), None)
        if winner is None:
            raise _not_found(item_id)
        return _loop_item(winner)
    wp = await twin.get_work_product(UUID(raw))
    if wp is None or wp.type != _WP_TYPES.get(kind):
        raise _not_found(item_id)
    return _wp_item(kind, wp)


def _uuid(raw: str, item_id: str) -> bool:
    try:
        UUID(raw)
    except ValueError as exc:
        raise _not_found(item_id) from exc
    return True


# ── decide ───────────────────────────────────────────────────────────────


async def decide(
    app: Any,
    item_id: str,
    *,
    decision: str,
    reason: str,
    to_phase: str,
    approver: Approver,
    surface: Surface,
    on_behalf_of: str | None,
    agent: str | None = None,
    base_revision: str | None = None,
) -> ApprovalItem:
    """Apply a decision through the same function the kind's old route uses.

    ``base_revision`` (FORGE-785, RT-030) is the item's revision as the
    decider saw it. When the item tracks one and it has moved on since, the
    decision is refused with 409: approving a proposal about a design that
    has changed would approve something nobody looked at. Callers that send
    none are unaffected.
    """
    kind, raw = split_id(item_id)
    current = await get_item(app, item_id)  # 404 first, and the not-decidable check
    if (
        surface == "agent"
        and current.kind in HUMAN_AUTHORITY_KINDS
        and not agent_may_decide_human_authority()
    ):
        raise HTTPException(status_code=403, detail=AGENT_HUMAN_AUTHORITY_REASON)
    if current.status != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"approval '{item_id}' is already {current.status}; nothing was recorded",
        )
    if current.not_decidable_reason and not current.decidable:
        raise HTTPException(status_code=409, detail=current.not_decidable_reason)
    if (
        base_revision is not None
        and current.base_revision is not None
        and base_revision != current.base_revision
    ):
        logger.info(
            "approval_decision_stale",
            approval_id=item_id,
            seen_revision=base_revision,
            current_revision=current.base_revision,
        )
        raise HTTPException(
            status_code=409,
            detail=(
                f"approval '{item_id}' has moved on since it was reviewed (reviewed revision "
                f"{base_revision}, current {current.base_revision}); review it again"
            ),
        )
    if decision in REASON_REQUIRED_FOR and not reason.strip():
        raise HTTPException(status_code=422, detail=f"'{decision}' needs a reason")
    offered = set(current.allowed_decisions)
    if kind == "gate":
        pass  # the gate's own rules (409/422) are applied by decide_run_gate
    elif decision not in offered:
        raise HTTPException(
            status_code=422,
            detail=f"'{decision}' is not available for a {kind} approval "
            f"(allowed: {', '.join(sorted(offered)) or 'none'})",
        )

    label = approver.label
    audit = {
        "verified": approver.verified,
        "surface": surface,
        "on_behalf_of": on_behalf_of,
        "agent": agent,
    }
    staged = _stage_run_decision(
        kind, raw, decision, reason, label, approver, surface, on_behalf_of, agent
    )
    try:
        if kind == "gate":
            await run_routes.decide_run_gate(
                raw, ApprovalDecision(decision), approver, reason=reason, to_phase=to_phase
            )
        elif kind == "tool":
            tool_approvals.decide_tool_approval(raw, decision, approver)
        elif kind == "change":
            await assistant_routes.decide_change(
                app,
                UUID(raw),
                ApprovalDecisionType(decision),
                reason=reason or f"{decision} via /v1/approvals",
                reviewer=label,
                reviewer_verified=approver.verified,
                surface=surface,
                on_behalf_of=on_behalf_of,
                agent=agent,
            )
        elif kind == "design_loop":
            await design_loop_routes.approve_loop(raw, label, audit)
        elif kind == "upload":
            await _decide_upload(app, raw, decision, reason, approver, surface)
        elif kind == "sketch":
            await twin_routes.approve_sketch_node(UUID(raw), label, audit)
        elif kind == "parametric_sketch":
            from api_gateway.twin.sketch_compiler_routes import approve_parametric_node

            await approve_parametric_node(UUID(raw), label, audit)
        elif kind == "rerun":
            from api_gateway.twin.impact_service import decide_rerun

            twin = _twin_of(app)
            try:
                await decide_rerun(twin, raw, decision, approver=label, reason=reason)
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        else:
            await twin_routes.approve_drawing_node(UUID(raw), label, audit)
    except Exception:
        _unstage_run_decision(staged)
        raise
    if kind == "gate" and surface != "reviewer" and current.review is not None:
        _record_agreement(current.review, decision)
    return await get_item(app, item_id)


def _stage_run_decision(
    kind: str,
    raw: str,
    decision: str,
    reason: str,
    label: str,
    approver: Approver,
    surface: str,
    on_behalf_of: str | None,
    agent: str | None = None,
) -> dict[str, Any] | None:
    """Write the decision into the run's request before the delegate moves it.

    The delegate's transition is what the run ledger persists, so the entry has
    to be on the record first. If the delegate refuses, the entry is removed
    again, so a refused decision leaves nothing behind.
    """
    if kind not in ("gate", "tool"):
        return None
    store = run_routes.get_run_store() if kind == "gate" else tool_approvals.get_approval_store()
    run = store.get(raw)
    entry: dict[str, Any] = DecisionRecord(
        decision=decision,
        reason=reason,
        approver=label,
        approver_verified=approver.verified,
        surface=surface,  # type: ignore[arg-type]
        on_behalf_of=on_behalf_of,
        agent=agent,
        decided_at=_iso(time.time()),
    ).model_dump()
    log = run.request.setdefault(DECISIONS_KEY, [])
    log.append(entry)
    return {"run": run, "entry": entry}


def _unstage_run_decision(staged: dict[str, Any] | None) -> None:
    if staged is None:
        return
    log = staged["run"].request.get(DECISIONS_KEY)
    if isinstance(log, list) and staged["entry"] in log:
        log.remove(staged["entry"])
    if not log:
        staged["run"].request.pop(DECISIONS_KEY, None)


# ── FORGE-726: the reviewer agent's notes, and the policy that acts on them ──

#: Where a run keeps its gate reviews (newest last), in ``run.request``.
REVIEWS_KEY = "gate_reviews"
_MAX_REVIEWS = 40
#: ``auto_clean`` approves only above this confidence; ``auto`` decides above the other.
AUTO_CLEAN_CONFIDENCE = 0.8
AUTO_CONFIDENCE = 0.6

_review_metrics: Any = None


def _metrics() -> Any:
    global _review_metrics  # noqa: PLW0603
    if _review_metrics is None:
        from observability.metrics import collector_for

        _review_metrics = collector_for("metaforge-gateway")
    return _review_metrics


def current_review(run: Run, snap: dict[str, Any] | None) -> GateReview | None:
    """The newest review of the gate that is open now (same phase and attempt)."""
    reviews = run.request.get(REVIEWS_KEY) or []
    phase = (snap or {}).get("phase")
    attempt = (snap or {}).get("attempt")
    for raw in reversed(reviews):
        try:
            review = GateReview.model_validate(raw)
        except Exception:  # noqa: BLE001 - a malformed entry is skipped, not fatal
            continue
        if phase is not None and review.phase != str(phase):
            continue
        if attempt is not None and review.attempt is not None and review.attempt != attempt:
            continue
        return review
    return None


def _record_agreement(review: GateReview, decision: str) -> None:
    agreed = review.recommendation == decision
    logger.info(
        "gate_review_agreement",
        recommendation=review.recommendation,
        decision=decision,
        agreed=agreed,
        reviewer=review.reviewer,
    )
    _metrics().record_gate_review_agreement(review.recommendation, decision, agreed)


async def record_review(app: Any, run_id: str, review: GateReview) -> GateReview:
    """Store a reviewer's note on the run, then let the project's policy act on it."""
    store = run_routes.get_run_store()
    run = store.get(run_id)
    if not review.created_at:
        review.created_at = datetime.now(UTC).isoformat()
    if not review.reviewer:
        review.reviewer = f"reviewer:{review.source}:{review.model or 'unknown'}"
    if review.recommendation == "rework" and review.to_phase in ("", review.phase):
        # FORGE-747: redoing the phase under review is a retry. A reviewer
        # that called it a rework of that phase (the Chair feasibility gate)
        # named a target no gate offers, so the policy could never apply it.
        logger.info("gate_review_rework_to_own_phase", run_id=run_id, phase=review.phase)
        review.recommendation, review.to_phase = "retry", ""
    applied, note = await _apply_policy(app, run, review)
    review.applied, review.applied_note = applied, note
    reviews = [*(run.request.get(REVIEWS_KEY) or []), review.model_dump()][-_MAX_REVIEWS:]
    store.annotate(run_id, REVIEWS_KEY, reviews)
    _metrics().record_gate_review(review.source, review.recommendation, applied)
    logger.info(
        "gate_review_recorded",
        run_id=run_id,
        phase=review.phase,
        recommendation=review.recommendation,
        confidence=review.confidence,
        reviewer=review.reviewer,
        model=review.model,
        model_source=review.model_source,
        applied=applied,
        note=note,
    )
    return review


async def _apply_policy(app: Any, run: Run, review: GateReview) -> tuple[str, str]:
    """``(applied, note)``: what the project's approval policy does with this review."""
    from api_gateway.approvals.policy import effective_mode, get_policy_store

    project_id = project_of(run.request)
    if not project_id:
        return "advised", "the run has no project, so a person decides"
    mode = effective_mode(await get_policy_store().get(str(project_id)))
    if mode in ("off", "advise"):
        return "advised", f"policy '{mode}': a person decides"
    if review.same_client:
        return "held", "reviewed by the client that did the phase, so it is advice only"
    if review.recommendation in ("none", "reject"):
        # A reviewer never ends a run on its own, and "none" is no view at all.
        return "held", f"the reviewer recommended '{review.recommendation}'; a person decides"
    try:
        item = await get_item(app, f"gate:{run.id}")
    except HTTPException as exc:
        return "held", f"the gate could not be read: {exc.detail}"
    if item.status != "pending" or not item.decidable:
        return "held", "the gate is not open for a decision"
    if item.detail.get("phase") not in (None, review.phase) or (
        review.attempt is not None and item.detail.get("attempt") not in (None, review.attempt)
    ):
        return "held", "the review is of an earlier gate"
    decision = review.recommendation
    if decision not in item.allowed_decisions:
        return "held", f"'{decision}' is not available at this gate"
    if mode == "auto_clean":
        if decision != "approve":
            return "held", "auto_clean only approves; a person decides retry and rework"
        if item.findings:
            return "held", "the gate has findings, so it is not clean"
        if review.confidence < AUTO_CLEAN_CONFIDENCE:
            return "held", f"confidence {review.confidence:.2f} is below {AUTO_CLEAN_CONFIDENCE}"
    elif review.confidence < AUTO_CONFIDENCE:
        return "held", f"confidence {review.confidence:.2f} is below {AUTO_CONFIDENCE}"
    if decision == "rework" and review.to_phase not in item.rework_targets:
        return "held", f"rework target '{review.to_phase}' is not one of {item.rework_targets}"
    reason = (review.reasons or f"The reviewer recommended {decision}.").strip()
    try:
        await decide(
            app,
            item.id,
            decision=decision,
            reason=f"[reviewer] {reason}"[:2000],
            to_phase=review.to_phase if decision == "rework" else "",
            approver=Approver(actor_id=review.reviewer, verified=False),
            surface="reviewer",
            on_behalf_of=None,
        )
    except HTTPException as exc:
        return "held", f"the decision was refused: {exc.detail}"
    return "decided", f"policy '{mode}': the reviewer chose '{decision}'"
