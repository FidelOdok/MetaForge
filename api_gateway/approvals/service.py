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
from collections import OrderedDict
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from fastapi import HTTPException

from api_gateway.approvals.findings import findings_from_lines, findings_from_reason
from api_gateway.approvals.schemas import (
    REASON_REQUIRED_FOR,
    ApprovalItem,
    DecisionRecord,
    GateFinding,
    Surface,
)
from api_gateway.assistant import routes as assistant_routes
from api_gateway.assistant.schemas import ApprovalDecisionType, ChangeStatus, DesignChangeProposal
from api_gateway.chat import tool_approvals
from api_gateway.design_loop import routes as design_loop_routes
from api_gateway.runs import routes as run_routes
from api_gateway.runs.schemas import project_of
from api_gateway.twin import routes as twin_routes
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

#: In-process record of who decided through this API and through what. Bounded
#: so a long-lived gateway does not grow it without limit; the decider is also
#: written to the underlying record by the delegate, and every decision is
#: logged, so this is the queryable copy and not the only one.
_AUDIT_LIMIT = 5000
_audit: OrderedDict[str, DecisionRecord] = OrderedDict()


def reset_audit() -> None:
    _audit.clear()


def record_audit(item_id: str, record: DecisionRecord) -> None:
    _audit[item_id] = record
    _audit.move_to_end(item_id)
    while len(_audit) > _AUDIT_LIMIT:
        _audit.popitem(last=False)


def _iso(value: Any) -> str | None:
    """ISO-8601 UTC from a ``datetime`` or an epoch-seconds float."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=UTC)).isoformat()
    return datetime.fromtimestamp(float(value), tz=UTC).isoformat()


def _record(item_id: str, fallback: DecisionRecord | None) -> DecisionRecord | None:
    return _audit.get(item_id) or fallback


_RUN_STATUS = {
    RunStatus.AWAITING_APPROVAL: "pending",
    RunStatus.TIMED_OUT: "expired",
    RunStatus.CANCELED: "canceled",
    RunStatus.REJECTED: "rejected",
}


def _run_status(run: Run, audited: DecisionRecord | None) -> str:
    """Contract status of a run-backed approval."""
    if run.status in _RUN_STATUS:
        return _RUN_STATUS[run.status]
    if audited is not None and audited.decision == "retry":
        return "retried"
    if audited is not None and audited.decision == "rework":
        return "reworked"
    return "approved"


def _run_fallback(run: Run) -> DecisionRecord | None:
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
    if pending:
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
        decision=record,
    )


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
    allowed = ["approve", "reject"] if pending and route != "elicitation" else []
    not_decidable: str | None = None
    if pending and route == "elicitation":
        not_decidable = (
            "this call is being answered in the client's own approval prompt; "
            "answer it there and it shows here as resolved"
        )
    if kind in ("flow_proposal", "flow_version"):
        detail: dict[str, Any] = {
            "flow_version_id": req.get("flow_version_id"),
            "intent": req.get("intent") or req.get("goal") or req.get("flow"),
            "changes": req.get("changes") or req.get("diff") or [],
        }
        title = "Flow version for approval" if kind == "flow_version" else "Flow proposal"
    else:
        detail = {"tool": req.get("tool"), "arguments": req.get("arguments") or {}}
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


def change_item(p: DesignChangeProposal) -> ApprovalItem:
    item_id = f"change:{p.change_id}"
    pending = p.status is ChangeStatus.PENDING
    fallback = (
        DecisionRecord(
            decision="approve" if p.status is not ChangeStatus.REJECTED else "reject",
            reason=p.decision_reason or "",
            approver=p.reviewer,
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
    )


# ── twin-backed kinds ────────────────────────────────────────────────────


def _twin_of(app: Any) -> Any | None:
    return getattr(app.state, "twin", None)


def _loop_item(winner: Any) -> ApprovalItem:
    item_id = f"design_loop:{winner.loop_id}"
    pending = not winner.approved
    fallback = (
        DecisionRecord(
            decision="approve", approver=winner.approved_by, decided_at=_iso(winner.approved_at)
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


def _wp_item(kind: str, wp: Any) -> ApprovalItem:
    item_id = f"{kind}:{wp.id}"
    approved = bool(wp.metadata.get("approved"))
    label = "Design sketch" if kind == "sketch" else "Technical drawing"
    fallback = (
        DecisionRecord(decision="approve", approver=wp.metadata.get("approved_by"))
        if approved
        else None
    )
    allowed = [] if approved else ["approve"]
    decidable, why = _decidable(not approved, allowed)
    return ApprovalItem(
        id=item_id,
        kind="sketch" if kind == "sketch" else "drawing",
        status="approved" if approved else "pending",
        title=f"{label}: {wp.name}",
        summary="" if approved else f"{label} awaiting sign-off before build work",
        project_id=str(wp.project_id) if wp.project_id else None,
        created_at=_iso(wp.created_at),
        route="dashboard",
        requested_by=wp.created_by,
        allowed_decisions=allowed,  # type: ignore[arg-type]
        decidable=decidable,
        not_decidable_reason=why,
        detail={"node_id": str(wp.id), "name": wp.name, "file_path": wp.file_path},
        decision=_record(item_id, fallback),
    )


async def _twin_items(app: Any) -> list[ApprovalItem]:
    twin = _twin_of(app)
    if twin is None:
        return []
    items: list[ApprovalItem] = []
    for kind, wp_type in (
        ("sketch", WorkProductType.DESIGN_SKETCH),
        ("drawing", WorkProductType.TECHNICAL_DRAWING),
    ):
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


# ── list / get ───────────────────────────────────────────────────────────


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
    items.extend(change_item(p) for p in assistant_routes.workflow.list_proposals())
    items.extend(await _twin_items(app))
    items.sort(key=lambda i: i.created_at or "", reverse=True)
    return items


async def _none() -> None:
    return None


def _not_found(item_id: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"approval '{item_id}' not found")


def split_id(item_id: str) -> tuple[str, str]:
    kind, sep, raw = item_id.partition(":")
    if (
        not sep
        or not raw
        or kind not in ("gate", "tool", "change", "design_loop", "sketch", "drawing")
    ):
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
        return change_item(proposal)
    twin = _twin_of(app)
    if twin is None:
        raise _not_found(item_id)
    _uuid(raw, item_id)
    if kind == "design_loop":
        iterations = await twin.list_design_loop_iterations(UUID(raw))
        winner = next((i for i in iterations if i.is_winner), None)
        if winner is None:
            raise _not_found(item_id)
        return _loop_item(winner)
    wp = await twin.get_work_product(UUID(raw))
    expected = (
        WorkProductType.DESIGN_SKETCH if kind == "sketch" else WorkProductType.TECHNICAL_DRAWING
    )
    if wp is None or wp.type != expected:
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
) -> ApprovalItem:
    """Apply a decision through the same function the kind's old route uses."""
    kind, raw = split_id(item_id)
    current = await get_item(app, item_id)  # 404 first, and the not-decidable check
    if current.status != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"approval '{item_id}' is already {current.status}; nothing was recorded",
        )
    if current.not_decidable_reason:
        raise HTTPException(status_code=409, detail=current.not_decidable_reason)
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
        )
    elif kind == "design_loop":
        await design_loop_routes.approve_loop(raw, label)
    elif kind == "sketch":
        await twin_routes.approve_sketch_node(UUID(raw), label)
    else:
        await twin_routes.approve_drawing_node(UUID(raw), label)

    record_audit(
        item_id,
        DecisionRecord(
            decision=decision,
            reason=reason,
            approver=label,
            approver_verified=approver.verified,
            surface=surface,
            on_behalf_of=on_behalf_of,
            decided_at=_iso(time.time()),
        ),
    )
    return await get_item(app, item_id)
