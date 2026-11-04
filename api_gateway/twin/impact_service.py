"""The gateway's side of cross-domain impact (FORGE-793).

twin_core walks the interface edges, marks results stale and records each
impact (:mod:`twin_core.impact.cross_domain`). It may not import the approval
policy, the tool annotations or the operation stream, so this module adds
them through the seams it offers:

* **Dispositions** (:func:`refine_dispositions`). Every proposed rerun starts
  ``requires_approval``. A rerun may run unattended only when it is not
  consequential (no firmware or PCB edit, nothing release-bound: XDM-007) and
  the existing approval rules already let that tool run without a person:
  the guardrails never hold a read-only tool (``kicad.run_drc``,
  ``kicad.run_erc``, ``firmware.static_check``), and a producing tool such
  as ``calculix.run_fea`` only under a project policy that approves it
  (``policy_may_approve``). A tool on the never list
  (``never_auto_approved``) always waits. Such a rerun is ``queued``: the
  agent or person may run it without asking. Nothing here starts a tool.
* **The operation event** (:func:`publish_recorded`). A recorded impact is one
  operation, ``job:impact-<id>``, in the ``cross_domain`` domain. Its first
  event is ``dependencies_invalidated``, sent as the contract type
  ``milestone`` with that stage id and the affected entities as
  ``artifact_refs``. Then ``review_required`` with one ``approval`` action per
  rerun that waits for a person, or ``completed`` when none does.
* **Rerun approvals** (:func:`rerun_items`, :func:`decide_rerun`). Each rerun
  waiting for a person is an item in ``/v1/approvals`` (``rerun:<impact>:<id>``,
  kind ``rerun``, a human-authority kind). Approving records the decision on
  the impact; the agent or person then runs the tool, and its new result
  supersedes the stale one through its revision pins. When no rerun is left
  waiting, the operation completes.
* **Pin map diffs and proposals**: the parameter extractor reads a pin map's
  pins (:mod:`api_gateway.twin.interface_proposals`), and after a head move
  of a board, enclosure, pin map or firmware item the proposer looks for new
  links from the recorded data.

:func:`install_impact_hooks` wires all of it; both the gateway and the MCP
sidecar call it at start-up, since either may move a head.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog

from observability.tracing import get_tracer
from twin_core.impact.cross_domain import (
    ImpactResult,
    ProposedRerun,
    count_impact,
    get_impact,
    list_impacts,
    save_impact,
)
from twin_core.models.enums import WorkProductType

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.impact_service")

#: The stage id the ``dependencies_invalidated`` milestone is sent with.
INVALIDATED_STAGE = "dependencies_invalidated"
#: Item types whose head move may reveal a new interface.
PROPOSE_ON = frozenset({"pcb_layout", "cad_model", "assembly", "pinmap", "firmware_source"})

#: Fixed sequences, so a retried publish of the same step is one event.
_SEQ = {"accepted": 10, "milestone": 20, "review_required": 30, "completed": 90}


class RerunNotFound(LookupError):
    pass


# ---------------------------------------------------------------------------
# Dispositions
# ---------------------------------------------------------------------------


async def _policy_mode(project_id: str) -> str:
    try:
        from api_gateway.approvals.policy import effective_mode, get_policy_store

        return effective_mode(await get_policy_store().get(project_id))
    except Exception as exc:  # noqa: BLE001 - no policy reads as "a person decides"
        logger.info("impact_policy_unavailable", project_id=project_id, error=str(exc))
        return "off"


def disposition_for(rerun: ProposedRerun, mode: str) -> tuple[str, str]:
    """``(disposition, why)`` for one rerun under the project's policy ``mode``."""
    from mcp_core.annotations import annotations_for
    from mcp_core.guardrails import never_auto_approved, policy_may_approve

    if rerun.consequential:
        return (
            "requires_approval",
            "a firmware or PCB change, or a release-bound target: a person decides",
        )
    if never_auto_approved(rerun.tool):
        return ("requires_approval", "no approval policy may approve this tool")
    try:
        read_only = bool(annotations_for(rerun.tool).get("readOnlyHint"))
    except Exception:  # noqa: BLE001 - an unknown tool is treated as a write
        read_only = False
    if read_only:
        return ("may_run_unattended", "a read-only check; the guardrails never hold it")
    if policy_may_approve(rerun.tool, mode):
        return ("may_run_unattended", f"the project's approval policy ({mode}) approves it")
    return ("requires_approval", f"the project's approval policy ({mode}) does not approve it")


async def refine_dispositions(_twin: Any, result: ImpactResult) -> ImpactResult:
    """Set each rerun's disposition from the policy; never loosens a decided one."""
    mode = await _policy_mode(result.project_id)
    reruns: list[ProposedRerun] = []
    for rerun in result.proposed_reruns:
        if rerun.status in ("approved", "rejected"):
            reruns.append(rerun)
            continue
        disposition, why = disposition_for(rerun, mode)
        reruns.append(
            rerun.model_copy(
                update={
                    "disposition": disposition,
                    "disposition_reason": why,
                    "status": "queued" if disposition == "may_run_unattended" else "proposed",
                }
            )
        )
    return result.model_copy(
        update={
            "proposed_reruns": reruns,
            "approval_policy": {
                "mode": mode,
                "rule": (
                    "consequential reruns (firmware and PCB edits, release-bound targets) "
                    "always wait for a person; others follow the project's approval policy"
                ),
            },
        }
    )


# ---------------------------------------------------------------------------
# The operation event
# ---------------------------------------------------------------------------


def rerun_approval_id(impact_id: str, rerun_id: str) -> str:
    return f"rerun:{impact_id}:{rerun_id}"


def _waiting(result: ImpactResult) -> list[ProposedRerun]:
    return [
        r
        for r in result.proposed_reruns
        if r.status == "proposed" and r.disposition == "requires_approval"
    ]


def _artifact_refs(result: ImpactResult) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for entity in result.impacted[:25]:
        node_id = entity.ref.removeprefix("record:") if entity.ref.startswith("record:") else None
        refs.append(
            {
                "kind": "work_product",
                "id": node_id or entity.ref,
                "label": f"{entity.label or entity.ref} ({entity.domain})"[:200],
            }
        )
    for stale in result.stale_results[:25]:
        refs.append(
            {
                "kind": "work_product",
                "id": stale.record_id,
                "label": f"{stale.name}: stale, was for {stale.pinned_ref}"[:200],
            }
        )
    return refs[:50]


def _base(result: ImpactResult) -> dict[str, Any]:
    assert result.operation_id is not None
    return {
        "operation_id": result.operation_id,
        "kind": "job",
        "domain": "cross_domain",
        "title": f"What {result.change.new_ref} affects"[:200],
        "base_revision": result.change.old_ref,
        "target_revision": result.change.new_ref,
    }


async def _publish(project_id: str, event: dict[str, Any]) -> bool:
    from mcp_core.operation_events import publish_operation_event

    return await publish_operation_event(project_id, event)


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


async def publish_recorded(_twin: Any, result: ImpactResult) -> None:
    """Publish the impact's operation: the milestone, then review or completion."""
    if result.operation_id is None:
        return
    base = _base(result)
    pid = result.project_id
    with tracer.start_as_current_span("twin.impact.publish") as span:
        span.set_attribute("impact.id", str(result.id))
        await _publish(
            pid,
            {
                **base,
                "event_id": f"{result.operation_id}:accepted",
                "sequence": _SEQ["accepted"],
                "type": "accepted",
                "state": "running",
                "message": "Checking what this change affects",
            },
        )
        await _publish(
            pid,
            {
                **base,
                "event_id": f"{result.operation_id}:{INVALIDATED_STAGE}",
                "sequence": _SEQ["milestone"],
                "type": "milestone",
                "state": "running",
                "stage_id": INVALIDATED_STAGE,
                "stage_title": "Dependencies invalidated",
                "message": (
                    f"{_plural(len(result.impacted), 'affected item')}, "
                    f"{_plural(len(result.stale_results), 'result')} out of date. "
                    f"{result.summary}"
                )[:500],
                "severity": "warning" if result.stale_results else "info",
                "artifact_refs": _artifact_refs(result),
            },
        )
        await _publish_outcome(result)


async def _publish_outcome(result: ImpactResult) -> None:
    waiting = _waiting(result)
    base = _base(result)
    assert result.id is not None
    if waiting:
        await _publish(
            result.project_id,
            {
                **base,
                "event_id": f"{result.operation_id}:review_required",
                "sequence": _SEQ["review_required"],
                "type": "review_required",
                "state": "waiting_for_user",
                "requires_action": True,
                "message": (
                    f"{_plural(len(waiting), 're-check')} waiting for your decision. "
                    f"{result.summary}"
                )[:500],
                "action_refs": [
                    {
                        "kind": "approval",
                        "id": rerun_approval_id(result.id, r.id),
                        "label": r.label[:200],
                    }
                    for r in waiting[:10]
                ],
            },
        )
        return
    await _publish(
        result.project_id,
        {
            **base,
            "event_id": f"{result.operation_id}:completed",
            "sequence": _SEQ["completed"],
            "type": "completed",
            "state": "completed",
            "message": (result.summary + " " + result.next_action)[:500],
        },
    )


# ---------------------------------------------------------------------------
# Rerun approvals
# ---------------------------------------------------------------------------


def split_rerun_id(raw: str) -> tuple[str, str]:
    impact_id, sep, rerun_id = raw.partition(":")
    if not sep or not rerun_id:
        raise RerunNotFound(raw)
    try:
        UUID(impact_id)
    except ValueError as exc:
        raise RerunNotFound(raw) from exc
    return impact_id, rerun_id


async def _impact_by_id(twin: Any, impact_id: str) -> ImpactResult:
    from twin_core.impact.cross_domain import impact_from_node

    node = await twin.graph.get_node(UUID(impact_id))
    found = impact_from_node(node) if node is not None else None
    if found is None:
        raise RerunNotFound(impact_id)
    return found


async def get_rerun(twin: Any, raw: str) -> tuple[ImpactResult, ProposedRerun]:
    impact_id, rerun_id = split_rerun_id(raw)
    impact = await _impact_by_id(twin, impact_id)
    rerun = next((r for r in impact.proposed_reruns if r.id == rerun_id), None)
    if rerun is None:
        raise RerunNotFound(raw)
    return impact, rerun


async def all_impacts(twin: Any) -> list[ImpactResult]:
    """Every recorded impact, every project (the approvals queue is global)."""
    from twin_core.impact.cross_domain import impact_from_node

    nodes = await twin.list_work_products(work_product_type=WorkProductType.CHANGE_IMPACT)
    return [r for r in (impact_from_node(n) for n in nodes) if r is not None]


async def decide_rerun(
    twin: Any, raw: str, decision: str, *, approver: str, reason: str = ""
) -> tuple[ImpactResult, ProposedRerun]:
    """Record a person's decision on one rerun; complete the operation when none waits."""
    impact, rerun = await get_rerun(twin, raw)
    if rerun.status != "proposed" or rerun.disposition != "requires_approval":
        raise ValueError(f"re-run {rerun.id} is already {rerun.status}; nothing was recorded")
    status = "approved" if decision == "approve" else "rejected"
    decided = rerun.model_copy(
        update={
            "status": status,
            "decided_by": approver,
            "decided_at": datetime.now(UTC).isoformat(),
            "decision_reason": reason or None,
        }
    )
    impact = impact.model_copy(
        update={
            "proposed_reruns": [decided if r.id == rerun.id else r for r in impact.proposed_reruns]
        }
    )
    await save_impact(twin, impact)
    count_impact("rerun_decided", status)
    logger.info(
        "impact_rerun_decided",
        impact_id=impact.id,
        rerun_id=rerun.id,
        tool=rerun.tool,
        target=rerun.target_ref,
        decision=status,
        decided_by=approver,
    )
    if not _waiting(impact):
        await _publish_outcome(impact)
    return impact, decided


# ---------------------------------------------------------------------------
# Head moves: propose new links from the data the move recorded
# ---------------------------------------------------------------------------


async def _recorded(twin: Any, result: ImpactResult) -> None:
    await publish_recorded(twin, result)


async def propose_after_head_move(twin: Any, item: Any) -> None:
    """Best effort: a new board, enclosure, pin map or firmware may reveal a link."""
    from api_gateway.twin.interface_proposals import propose_interfaces

    if getattr(item, "item_type", "") not in PROPOSE_ON or item.project_id is None:
        return
    try:
        await propose_interfaces(twin, item.project_id, item_key=item.key)
    except Exception as exc:  # noqa: BLE001 - a proposal never fails a write
        logger.warning("interface_proposal_after_move_failed", item_key=item.key, error=str(exc))


def install_impact_hooks() -> None:
    """Wire the policy refiner, the event publisher and the pin map diff into twin_core."""
    from api_gateway.twin.interface_proposals import pinmap_parameter_diff
    from twin_core.impact.cross_domain import set_impact_hooks, set_parameter_extractor

    set_impact_hooks(
        refine=refine_dispositions, recorded=_recorded, head_moved=propose_after_head_move
    )
    set_parameter_extractor(pinmap_parameter_diff)
    logger.info("impact_hooks_installed")


__all__ = [
    "INVALIDATED_STAGE",
    "RerunNotFound",
    "all_impacts",
    "decide_rerun",
    "disposition_for",
    "get_impact",
    "get_rerun",
    "install_impact_hooks",
    "list_impacts",
    "propose_after_head_move",
    "publish_recorded",
    "refine_dispositions",
    "rerun_approval_id",
    "split_rerun_id",
]
