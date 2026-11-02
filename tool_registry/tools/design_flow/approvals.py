"""Pending approvals in the client's chat (FORGE-722).

``approvals.list`` shows what is waiting for a person: design-flow gates, held
tool calls, change proposals and twin approvals, the same items the dashboard's
Approvals page and ``forge approvals`` show. ``approvals.decide`` puts ONE of
them to the *person* in the client's own prompt (MCP elicitation), exactly as
``flow.await_gate`` does for a run's gate. The agent picks which item to ask
about; it cannot answer the prompt, so this approves nothing by itself, which
keeps FORGE-400's rule that an agent has no tool that approves its own work.

The answer goes through the gateway's ``service.decide``, so the dashboard's
rules hold: only the decisions an item allows right now are offered, approve is
not offered on a gate whose checks failed, and reject, retry and rework need a
reason. Dismissing or declining the prompt is nobody's decision, and the item
stays pending.

Layering: the gateway is reached through injected callables, like the rest of
this adapter.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import structlog

from mcp_core.elicitation import ElicitAction, current_elicitor, inline_approver
from observability.tracing import get_tracer
from tool_registry.tools.design_flow.gates import (
    DEFAULT_ANSWER_SECONDS,
    MAX_ANSWER_SECONDS,
    GateDecider,
    review_lines,
)

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.tools.design_flow.approvals")

__all__ = ["ApprovalsLister", "approval_question", "decide_approval", "list_pending"]

#: ``(project_id | None) -> [<approval item dict>, ...]``: the pending items,
#: newest first, scoped to the project when one is given.
ApprovalsLister = Callable[[str | None], Awaitable[list[dict[str, Any]]]]

_DECISION_WORDS = {
    "approve": "approve: let it go ahead",
    "retry": "retry: run the phase again with your reason as feedback",
    "rework": "rework: send the run back to an earlier phase (choose it below)",
    "reject": "reject: refuse it",
}

_ASK_ELSEWHERE = (
    "Ask the person to answer it in the MetaForge dashboard or with `forge approvals`. "
    "You cannot decide it yourself, and must not tell the user it was decided."
)


def _summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("id"),
        "kind": item.get("kind"),
        "title": item.get("title"),
        "summary": item.get("summary") or item.get("reason_held") or "",
        "project_id": item.get("project_id"),
        "created_at": item.get("created_at"),
        "deadline": item.get("deadline"),
        "decidable": bool(item.get("decidable")),
        "allowed_decisions": list(item.get("allowed_decisions") or []),
        "not_decidable_reason": item.get("not_decidable_reason"),
        "review": item.get("review"),
    }


async def list_pending(*, lister: ApprovalsLister, project_id: str | None = None) -> dict[str, Any]:
    """The pending approvals, summarised for the agent to show the person."""
    with tracer.start_as_current_span("approvals.list") as span:
        items = [i for i in await lister(project_id) if i.get("status", "pending") == "pending"]
        span.set_attribute("approvals.count", len(items))
    logger.info("approvals_listed_in_chat", count=len(items), project_id=project_id)
    return {
        "count": len(items),
        "items": [_summary(i) for i in items],
        "message": (
            "Nothing is waiting for a decision."
            if not items
            else "Show these to the person. To have them decide one here, call "
            "approvals.decide with its id; they answer in a prompt, you do not."
        ),
    }


def approval_question(item: dict[str, Any], answer_seconds: float) -> tuple[str, dict[str, Any]]:
    """The prompt and form for one pending item, offering only what it allows."""
    allowed = [d for d in item.get("allowed_decisions") or [] if d in _DECISION_WORDS]
    kind = str(item.get("kind") or "approval").replace("_", " ")
    lines = [f"MetaForge {kind} waiting for you: {item.get('title') or item.get('id')}"]
    summary = str(item.get("summary") or item.get("reason_held") or "").strip()
    if summary:
        lines += ["", summary]
    findings = item.get("findings") or []
    if findings:
        lines += ["", "What the checks found:"]
        lines += [f"  - [{f.get('severity', 'error')}] {f.get('message', '')}" for f in findings]
    lines += review_lines(item)
    lines += ["", "Choices:"] + [f"  - {_DECISION_WORDS[d]}" for d in allowed]
    if item.get("kind") == "gate" and "approve" not in allowed:
        lines += ["", "Approve is not offered: the gate's checks did not pass."]
    required = [d for d in item.get("reason_required_for") or [] if d in allowed]
    if required:
        lines += ["", f"{', '.join(required)} need a reason."]
    lines += [
        "",
        f"Answer within {answer_seconds:.0f} seconds; after that it stays pending in "
        "the MetaForge dashboard.",
    ]
    properties: dict[str, Any] = {
        "decision": {"type": "string", "title": "Decision", "enum": allowed},
        "reason": {
            "type": "string",
            "title": "Reason",
            "description": "Recorded with the decision. Required for reject, retry and rework.",
            "maxLength": 2000,
        },
    }
    targets = [str(t) for t in item.get("rework_targets") or []]
    if "rework" in allowed and targets:
        properties["to_phase"] = {
            "type": "string",
            "title": "Rework: phase to go back to",
            "enum": targets,
        }
    return "\n".join(lines), {
        "type": "object",
        "properties": properties,
        "required": ["decision"],
    }


def _open(item: dict[str, Any] | None, message: str) -> dict[str, Any]:
    return {
        "status": "pending",
        "approval": _summary(item) if item else None,
        "message": message,
    }


async def decide_approval(
    *,
    lister: ApprovalsLister,
    decider: GateDecider,
    approval_id: str,
    answer_seconds: float | None = None,
) -> dict[str, Any]:
    """Ask the person to decide one pending item, and record their answer."""
    with tracer.start_as_current_span("approvals.decide") as span:
        span.set_attribute("approval.id", approval_id)
        item = next((i for i in await lister(None) if i.get("id") == approval_id), None)
        if item is None or item.get("status", "pending") != "pending":
            return _open(None, f"No pending approval '{approval_id}'. Call approvals.list.")
        if not item.get("decidable"):
            reason = item.get("not_decidable_reason") or "it cannot be decided now"
            return _open(item, f"It is pending but {reason}. {_ASK_ELSEWHERE}")
        elicitor = current_elicitor()
        if elicitor is None:
            return _open(item, f"This client cannot show a prompt. {_ASK_ELSEWHERE}")

        try:
            answer = min(
                max(float(answer_seconds or DEFAULT_ANSWER_SECONDS), 1.0), MAX_ANSWER_SECONDS
            )
        except (TypeError, ValueError):
            answer = DEFAULT_ANSWER_SECONDS
        message, schema = approval_question(item, answer)
        try:
            result = await asyncio.wait_for(elicitor(message, schema), timeout=answer)
        except TimeoutError:
            return _open(item, f"Nobody answered in time. {_ASK_ELSEWHERE}")
        if result.action is not ElicitAction.ACCEPT:
            return _open(
                item, f"The person did not answer ({result.action.value}). {_ASK_ELSEWHERE}"
            )

        decision = str(result.content.get("decision") or "")
        reason = str(result.content.get("reason") or "").strip()
        to_phase = str(result.content.get("to_phase") or "")
        if decision not in (item.get("allowed_decisions") or []):
            return _open(item, f"'{decision}' is not a decision this item allows.")
        if decision in (item.get("reason_required_for") or ()) and not reason:
            return _open(
                item, f"'{decision}' needs a reason and none was given; it is still pending."
            )
        approver = inline_approver()
        try:
            decided = await decider(
                approval_id, decision, reason, to_phase, approver.actor_id, approver.verified
            )
        except Exception as exc:  # noqa: BLE001 - the gateway's refusal is the answer
            logger.warning("approvals_decide_refused", approval_id=approval_id, error=str(exc))
            return _open(item, f"The gateway refused the decision: {exc}")
        record = decided.get("decision") or {}
        logger.info(
            "approvals_decided_in_chat",
            approval_id=approval_id,
            kind=item.get("kind"),
            decision=decision,
            decided_by=record.get("approver"),
        )
        return {
            "status": "decided",
            "approval_id": approval_id,
            "kind": item.get("kind"),
            "decision": decision,
            "decided_by": record.get("approver") or approver.actor_id,
            "message": f"The person chose '{decision}'.",
        }
