"""Putting a design-flow gate to the person in the client's chat (FORGE-582).

``flow.await_gate`` waits until a run reaches a gate and then asks the
*person* in the client's own prompt (MCP elicitation), not the agent. The
agent calls the tool; the answer comes from whoever is at the keyboard, and
is recorded with that person as the approver, exactly as a dashboard click
would be. That is what keeps this on the right side of FORGE-400's rule that
an agent has no tool that approves its own work: this tool approves nothing
by itself, it asks.

The decision goes through the gateway's ``service.decide``, so every rule a
dashboard click meets applies here too: approve is not offered on a gate
that is not ready, retry and rework are capped, and reject, retry and rework
need a reason.

Answers that are not a decision leave the gate open. Dismissing the prompt,
declining it, or letting it expire is nobody's decision, and the gate stays
in the dashboard queue for someone to answer there.

Layering: the gateway is reached through injected callables, like the rest
of this adapter.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

import structlog

from mcp_core.elicitation import ElicitAction, current_elicitor, inline_approver
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.tools.design_flow.gates")

__all__ = [
    "DEFAULT_ANSWER_SECONDS",
    "DEFAULT_WAIT_SECONDS",
    "GateDecider",
    "GateReader",
    "await_gate",
    "gate_question",
]

#: ``(run_id) -> {"run_status": str, "gate": <approval item dict> | None}``.
GateReader = Callable[[str], Awaitable[dict[str, Any]]]

#: ``(approval_id, decision, reason, to_phase, approver, approver_verified)``
#: ``-> <approval item dict>``. Raises with the gateway's reason on refusal.
GateDecider = Callable[[str, str, str, str, str | None, bool], Awaitable[dict[str, Any]]]

#: How long the tool waits for the run to reach a gate before handing back.
DEFAULT_WAIT_SECONDS = 120.0
MAX_WAIT_SECONDS = 1800.0
#: How long the person has to answer the prompt.
DEFAULT_ANSWER_SECONDS = 300.0
MAX_ANSWER_SECONDS = 1800.0
_POLL_SECONDS = 3.0

_TERMINAL = ("completed", "failed", "rejected", "canceled", "timed_out")

_DECISION_WORDS = {
    "approve": "approve: accept this phase and continue the run",
    "retry": "retry: run this phase again with your reason as feedback",
    "rework": "rework: send the run back to an earlier phase (choose it below)",
    "reject": "reject: end the run here",
}

#: Said whenever the person could not be asked here. The agent must not
#: answer in their place.
_ASK_ELSEWHERE = (
    "Ask the person to answer it in the MetaForge dashboard or with `forge approvals`. "
    "You cannot decide a gate yourself, and must not tell the user it was decided."
)


def _bounded(value: Any, default: float, upper: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return min(max(number, 1.0), upper)


def gate_question(gate: dict[str, Any], answer_seconds: float) -> tuple[str, dict[str, Any]]:
    """The prompt and form for one open gate.

    The form stays inside what elicitation allows (a flat object of
    primitives, string enums included), and offers only the decisions the
    gate allows right now.
    """
    allowed = [d for d in gate.get("allowed_decisions") or [] if d in _DECISION_WORDS]
    lines = [f"MetaForge design-flow gate: {gate.get('title') or gate.get('id')}"]
    summary = str(gate.get("summary") or "").strip()
    if summary:
        lines += ["", summary]
    findings = gate.get("findings") or []
    if findings:
        lines += ["", "What the gate found:"]
        lines += [f"  - [{f.get('severity', 'error')}] {f.get('message', '')}" for f in findings]
    detail = gate.get("detail") or {}
    if detail.get("retries_left") is not None:
        lines += ["", f"Retries left for this phase: {detail['retries_left']}"]
    lines += ["", "Choices:"] + [f"  - {_DECISION_WORDS[d]}" for d in allowed]
    if "approve" not in allowed:
        lines += ["", "Approve is not offered: the gate's checks did not pass."]
    lines += [
        "",
        "Reject, retry and rework need a reason.",
        f"Answer within {answer_seconds:.0f} seconds; after that the gate stays open "
        "in the MetaForge dashboard.",
    ]
    properties: dict[str, Any] = {
        "decision": {
            "type": "string",
            "title": "Decision",
            "enum": allowed,
        },
        "reason": {
            "type": "string",
            "title": "Reason",
            "description": "Required for reject, retry and rework. Recorded with the decision.",
            "maxLength": 2000,
        },
    }
    targets = [str(t) for t in gate.get("rework_targets") or []]
    if "rework" in allowed and targets:
        properties["to_phase"] = {
            "type": "string",
            "title": "Rework: phase to go back to",
            "enum": targets,
        }
    schema = {"type": "object", "properties": properties, "required": ["decision"]}
    return "\n".join(lines), schema


def _open_gate_view(gate: dict[str, Any], message: str) -> dict[str, Any]:
    return {
        "status": "awaiting_gate",
        "approval_id": gate.get("id"),
        "title": gate.get("title"),
        "findings": [f.get("message") for f in gate.get("findings") or []],
        "allowed_decisions": gate.get("allowed_decisions") or [],
        "message": message,
    }


async def await_gate(
    run_id: str,
    *,
    reader: GateReader,
    decider: GateDecider,
    wait_seconds: Any = None,
    answer_seconds: Any = None,
    poll_seconds: float = _POLL_SECONDS,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Wait for ``run_id``'s next gate and ask the person to decide it."""
    wait = _bounded(wait_seconds, DEFAULT_WAIT_SECONDS, MAX_WAIT_SECONDS)
    answer = _bounded(answer_seconds, DEFAULT_ANSWER_SECONDS, MAX_ANSWER_SECONDS)
    with tracer.start_as_current_span("flow.await_gate") as span:
        span.set_attribute("run.id", run_id)
        deadline = clock() + wait
        while True:
            state = await reader(run_id)
            status = str(state.get("run_status") or "")
            gate = state.get("gate")
            if isinstance(gate, dict) and gate.get("status") == "pending":
                break
            if status in _TERMINAL:
                return {
                    "status": "run_ended",
                    "run_status": status,
                    "message": f"The run is {status}; there is no gate to answer.",
                }
            if clock() >= deadline:
                return {
                    "status": "no_gate_yet",
                    "run_status": status,
                    "message": (
                        f"No gate opened within {wait:.0f} seconds; the run is {status or 'busy'}. "
                        "Call flow.await_gate again to keep waiting."
                    ),
                }
            await sleep(poll_seconds)

        span.set_attribute("approval.id", str(gate.get("id")))
        if not gate.get("decidable"):
            reason = gate.get("not_decidable_reason") or "this gate cannot be decided now"
            return _open_gate_view(gate, f"The gate is open but {reason}. {_ASK_ELSEWHERE}")
        elicitor = current_elicitor()
        if elicitor is None:
            logger.info("flow_await_gate_no_prompt", run_id=run_id, approval_id=gate.get("id"))
            return _open_gate_view(
                gate, f"A gate is waiting, and this client cannot show a prompt. {_ASK_ELSEWHERE}"
            )

        message, schema = gate_question(gate, answer)
        try:
            result = await asyncio.wait_for(elicitor(message, schema), timeout=answer)
        except TimeoutError:
            logger.info("flow_await_gate_unanswered", run_id=run_id, why="expired")
            return _open_gate_view(gate, f"Nobody answered in time. {_ASK_ELSEWHERE}")
        if result.action is not ElicitAction.ACCEPT:
            # Dismissed or declined: nobody chose a decision, so the gate stays
            # open. Declining the prompt is not "reject the run".
            logger.info("flow_await_gate_unanswered", run_id=run_id, why=result.action.value)
            return _open_gate_view(
                gate, f"The person did not answer here ({result.action.value}). {_ASK_ELSEWHERE}"
            )

        decision = str(result.content.get("decision") or "")
        reason = str(result.content.get("reason") or "").strip()
        to_phase = str(result.content.get("to_phase") or "")
        if decision not in (gate.get("allowed_decisions") or []):
            return _open_gate_view(gate, f"'{decision}' is not a decision this gate allows.")
        if decision in (gate.get("reason_required_for") or ()) and not reason:
            return _open_gate_view(
                gate, f"'{decision}' needs a reason and none was given; the gate is still open."
            )
        approver = inline_approver()
        try:
            item = await decider(
                str(gate["id"]),
                decision,
                reason,
                to_phase,
                approver.actor_id,
                approver.verified,
            )
        except Exception as exc:  # noqa: BLE001 - the gateway's refusal is the answer
            logger.warning("flow_await_gate_refused", run_id=run_id, error=str(exc))
            return _open_gate_view(gate, f"The gateway refused the decision: {exc}")
        record = item.get("decision") or {}
        logger.info(
            "flow_await_gate_decided",
            run_id=run_id,
            decision=decision,
            decided_by=record.get("approver"),
        )
        return {
            "status": "decided",
            "approval_id": gate.get("id"),
            "decision": decision,
            "decided_by": record.get("approver") or approver.actor_id,
            "approval_status": item.get("status"),
            "message": (
                f"The person chose '{decision}'. "
                + (
                    "The run continues; call flow.await_gate again for the next gate."
                    if decision in ("approve", "retry", "rework")
                    else "The run has ended."
                )
            ),
        }
