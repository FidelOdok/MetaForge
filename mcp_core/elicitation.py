"""Ask the human inside the harness, when the harness can ask (FORGE-360).

F1 (:mod:`mcp_core.guardrails`) decides which calls are held and parks them
in the dashboard queue. That works when someone has the dashboard open. A
stdio session on an engineer's laptop usually does not — which is why
``_EXEMPTIBLE`` in that module exempts local writes by default, with a
comment saying the exemption exists only because stdio has nowhere to
answer an approval, and should go away once F2 lands.

This is F2. MCP's ``elicitation/create`` (protocol revision 2025-06-18) lets
a server ask its client to put a question to the user, so the harness that
is already in front of the engineer becomes the approval surface. Claude
Code and Codex render it inline; the answer comes back on the same
connection that asked.

Two things it deliberately does not do:

* **It does not replace the dashboard queue.** A client that cannot elicit
  falls back to it. One approval ledger, two ways to answer.
* **It does not put the tool arguments on screen unredacted.** The spec is
  explicit that servers MUST NOT request sensitive information through
  elicitation, and the mirror of that is not shipping any either: a held
  call's arguments can carry an API key, and a reviewer does not need to
  see it to say yes.

Layer-1 module: stdlib only.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

import structlog

from mcp_core.guardrails import ApprovalAsk, ApprovalGateFn, ApprovalOutcome

logger = structlog.get_logger(__name__)

__all__ = [
    "ELICITATION_PROTOCOL_VERSION",
    "ElicitAction",
    "ElicitResult",
    "Elicitor",
    "ElicitationUnavailableError",
    "approval_request",
    "cancelled_notification",
    "elicitation_gate",
    "result_from_payload",
]

#: The protocol revision that introduced ``elicitation/create``. A client
#: that declared the capability but negotiated an older revision is not
#: expecting the request, so the server checks both.
ELICITATION_PROTOCOL_VERSION = "2025-06-18"

#: Argument names whose values never reach the reviewer's screen. Same list
#: as ``metaforge.mcp.capture``, for the same reason and to the same effect.
_REDACT_HINTS = ("key", "secret", "token", "password", "credential")

#: Arguments are context, not the payload. A 60 KB CAD script tells a
#: reviewer nothing a summary does not, and some clients render an
#: elicitation prompt in a box the size of a tooltip.
_MAX_VALUE_CHARS = 120
_MAX_ARGS_SHOWN = 8


class ElicitAction(StrEnum):
    """The three ways a client can answer ``elicitation/create``."""

    #: The user submitted the form. ``content`` carries their answer -- which
    #: may still be "no".
    ACCEPT = "accept"
    #: The user explicitly refused the request itself.
    DECLINE = "decline"
    #: Dismissed without choosing: closed the dialog, pressed Escape, or the
    #: client gave up. Nobody said no; nobody said yes either.
    CANCEL = "cancel"


@dataclass(frozen=True)
class ElicitResult:
    action: ElicitAction
    content: dict[str, Any] = field(default_factory=dict)


class Elicitor(Protocol):
    """Sends one ``elicitation/create`` and waits for the client's answer.

    Implemented by the transport, because only the transport has a channel
    back to the client: stdio writes a request on stdout and matches the
    reply by id. A transport with no server-to-client direction (plain HTTP
    POST, no SSE) simply has no implementation, and the gate falls back.
    """

    def __call__(
        self, message: str, requested_schema: dict[str, Any]
    ) -> Awaitable[ElicitResult]: ...


class ElicitationUnavailableError(RuntimeError):
    """The client cannot be asked. Raised so a caller can fall back.

    Never swallowed into an approval outcome: "the client could not be
    asked" and "the user said no" are different facts, and only one of them
    should stop the call.
    """


def _summarise_arguments(arguments: dict[str, Any]) -> list[str]:
    """One ``name: value`` line per argument, redacted and clipped."""
    lines: list[str] = []
    for name, value in list(arguments.items())[:_MAX_ARGS_SHOWN]:
        if any(hint in name.lower() for hint in _REDACT_HINTS):
            lines.append(f"  {name}: <redacted>")
            continue
        if isinstance(value, str):
            rendered = value
        else:
            rendered = json.dumps(value, default=str)
        if len(rendered) > _MAX_VALUE_CHARS:
            rendered = rendered[:_MAX_VALUE_CHARS] + f"… (+{len(rendered) - _MAX_VALUE_CHARS})"
        lines.append(f"  {name}: {rendered}")
    remaining = len(arguments) - _MAX_ARGS_SHOWN
    if remaining > 0:
        lines.append(f"  … and {remaining} more argument{'s' if remaining > 1 else ''}")
    return lines


def approval_request(ask: ApprovalAsk) -> tuple[str, dict[str, Any]]:
    """The ``message`` and ``requestedSchema`` for one held call.

    ``requestedSchema`` stays inside the subset the spec allows -- a flat
    object of primitives -- because anything richer is not merely
    discouraged, it is something clients are not required to render.
    """
    lines = [
        f"MetaForge wants to run {ask.tool_id}.",
        "",
        ask.reason,
    ]
    if ask.project:
        # Named before the arguments, not after: once a project is set with
        # project.open the calls that follow carry no project of their own,
        # so this is the only place a reviewer learns where the write lands.
        lines += ["", f"Project: {ask.project}"]
    if ask.arguments:
        lines += ["", "Arguments:", *_summarise_arguments(ask.arguments)]
    lines += ["", f"Requested by: {ask.caller.value}"]
    if ask.timeout_seconds is not None:
        # FORGE-472: the person is told the deadline the server will keep.
        # Without it a form that quietly stopped mattering looked exactly like
        # one still waiting for an answer.
        lines += [
            "",
            f"Answer within {ask.timeout_seconds:.0f} seconds. After that this "
            f"request expires and {ask.tool_id} is not run.",
        ]
    message = "\n".join(lines)

    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "approve": {
                "type": "boolean",
                "title": f"Run {ask.tool_id}?",
                "description": "Yes runs the tool now. No refuses it.",
            },
            "note": {
                "type": "string",
                "title": "Note (optional)",
                "description": "Recorded with the decision.",
                "maxLength": 280,
            },
        },
        "required": ["approve"],
    }
    return message, schema


def result_from_payload(payload: dict[str, Any]) -> ElicitResult:
    """Map one JSON-RPC response to ``elicitation/create`` onto a result.

    Transport-agnostic, and shared (FORGE-423) because the three defaults
    below are the easy part to get wrong and getting them wrong in one
    transport and not the other is worse than getting them wrong in both:

    * an ``error`` response means the client could not put the question.
      Not an answer, so it must not read as one.
    * an ``action`` the spec does not define is a client that did not honour
      the contract. Also not an answer.
    * both resolve to ``cancel``, which the gate maps to ``TIMED_OUT`` --
      "nobody looked", rather than "a reviewer said no".

    Every unknown resolves toward *not* having an answer, because the
    failure mode in the other direction is an unreviewed write.
    """
    if "error" in payload:
        logger.warning("elicitation_client_error", error=payload["error"])
        return ElicitResult(ElicitAction.CANCEL)
    result = payload.get("result") or {}
    raw_action = result.get("action")
    try:
        action = ElicitAction(raw_action if isinstance(raw_action, str) else "")
    except ValueError:
        logger.warning("elicitation_bad_action", action=raw_action)
        return ElicitResult(ElicitAction.CANCEL)
    content = result.get("content")
    return ElicitResult(action, content if isinstance(content, dict) else {})


def cancelled_notification(request_id: str | int, reason: str) -> dict[str, Any]:
    """A ``notifications/cancelled`` withdrawing one ``elicitation/create``.

    FORGE-472. Sent by the server when it stops waiting on a question it
    asked, so the client can take the form down instead of leaving a prompt
    on screen whose answer will be thrown away.
    """
    return {
        "jsonrpc": "2.0",
        "method": "notifications/cancelled",
        "params": {"requestId": request_id, "reason": reason},
    }


def elicitation_gate(elicitor: Elicitor) -> ApprovalGateFn:
    """An :data:`ApprovalGateFn` that asks through the connected client.

    The three-action response maps onto the outcomes F1 already defines,
    and the mapping is the whole point of keeping three actions rather than
    a boolean:

    * ``accept`` with ``approve: true`` -- approved.
    * ``accept`` with ``approve: false`` -- the user answered the question
      and the answer was no. Rejected.
    * ``decline`` -- the user refused the prompt itself. Also rejected: they
      were asked and did not allow it.
    * ``cancel`` -- dismissed. Nobody decided, so this is ``TIMED_OUT``, the
      outcome F1 created precisely so an agent is not told "a reviewer
      rejected it" when no reviewer looked.

    ``ask.timeout_seconds`` bounds the wait (FORGE-472), so the server
    resolves inside the client's own tool timeout. The elicitor sees the
    deadline as a cancellation, which is where it withdraws the question.
    """

    async def gate(ask: ApprovalAsk) -> ApprovalOutcome:
        message, schema = approval_request(ask)
        try:
            result = await asyncio.wait_for(elicitor(message, schema), timeout=ask.timeout_seconds)
        except TimeoutError:
            logger.info(
                "elicitation_window_ended", tool_id=ask.tool_id, window_seconds=ask.timeout_seconds
            )
            return ApprovalOutcome.TIMED_OUT
        if result.action is ElicitAction.CANCEL:
            return ApprovalOutcome.TIMED_OUT
        if result.action is ElicitAction.DECLINE:
            return ApprovalOutcome.REJECTED
        approved = result.content.get("approve")
        # A missing or non-boolean ``approve`` on an accept is a client that
        # did not honour the schema. Treated as a refusal, not an approval:
        # the failure mode of guessing wrong in the other direction is an
        # unreviewed write.
        return ApprovalOutcome.APPROVED if approved is True else ApprovalOutcome.REJECTED

    return gate


def first_available_gate(
    *gates: ApprovalGateFn | None,
) -> ApprovalGateFn | None:
    """The first configured gate, for wiring elicitation ahead of the queue.

    A plain helper rather than a composite that retries: falling through to
    the dashboard *after* a client has already answered would ask a second
    person the same question, and the first answer would be lost.
    """
    for gate in gates:
        if gate is not None:
            return gate
    return None


ElicitorFactory = Callable[[], Elicitor | None]
