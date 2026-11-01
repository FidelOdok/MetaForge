"""Hold an MCP tool call in the queue a human is already watching (FORGE-359).

``mcp_core.guardrails`` decides *whether* a call needs approval. This is the
other half: parking it somewhere a person can see it and answer.

It deliberately reuses the store behind ``/v1/chat/tool_approvals`` rather
than standing up a second one. The spec asks for one approval queue per
project, independent of which client asked (§4), and the dashboard's
Approvals page already reads that queue — a separate MCP queue would mean a
held write sitting in a list nobody has open.

The URL says ``chat`` because that is where the endpoint started. That is a
naming wart, not a scope statement; the store is the process-level approval
ledger and this is a second, equally legitimate producer for it.
"""

from __future__ import annotations

import asyncio

import structlog

from api_gateway.chat.tool_approvals import (
    HOLD_DEADLINE_GRACE_SECONDS,
    get_approval_store,
    record_inline_answer,
)
from mcp_core.guardrails import (
    ApprovalAsk,
    ApprovalGateFn,
    ApprovalLedgerUnavailableError,
    ApprovalOutcome,
    ApprovalResolution,
    Approver,
    effective_hold_window,
    notify_held,
)
from orchestrator.harness.runs import ApprovalWait, RunStatus, await_approval_decision

logger = structlog.get_logger(__name__)

#: How long a held MCP call waits before it is denied. Shorter than a chat
#: approval on purpose: an MCP client is holding a request open, and most
#: will give up on their own well before a generous server-side window
#: expires — at which point the human's click lands on a call nobody is
#: waiting for any more.
#:
#: Used only when the server did not choose a window on the ask (FORGE-465:
#: it normally does, shorter when the client cannot be sent progress).
DEFAULT_TIMEOUT_SECONDS = 180.0
DEFAULT_POLL_INTERVAL = 0.5

_OUTCOMES: dict[ApprovalWait, ApprovalOutcome] = {
    ApprovalWait.APPROVED: ApprovalOutcome.APPROVED,
    ApprovalWait.REJECTED: ApprovalOutcome.REJECTED,
    ApprovalWait.TIMED_OUT: ApprovalOutcome.TIMED_OUT,
}


def build_mcp_approval_gate(
    *,
    timeout_seconds: float | None = None,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
) -> ApprovalGateFn:
    """An approval gate the MCP server can be constructed with.

    Returns a callable, not a class, because that is the whole seam: the
    MCP server must not import this module, and anything it does import from
    the gateway layer is a layering violation waiting to be reintroduced.

    ``timeout_seconds``, when given, caps the window the server asks for
    (FORGE-465); left out, the ask's window applies.
    """

    async def gate(ask: ApprovalAsk) -> ApprovalResolution:
        window = effective_hold_window(timeout_seconds, ask, DEFAULT_TIMEOUT_SECONDS)
        store = get_approval_store()
        run = store.create(
            {
                "tool": ask.tool_id,
                "arguments": ask.arguments,
                # Who asked matters to whoever is deciding. A write from a
                # remote harness and the same write from the dashboard are
                # not the same request to a reviewer.
                "caller": ask.caller.value,
                "source": "mcp",
                "session_id": ask.session_id,
                "route": "dashboard",
                "client": ask.client,
            }
        )
        store.start(run.id)
        # FORGE-466: the wait below closes its own hold however it ends; the
        # deadline is the backstop the ledger's readers expire it by if not.
        store.request_approval(
            run.id,
            reason=ask.reason,
            deadline=store.now() + window + HOLD_DEADLINE_GRACE_SECONDS,
        )
        logger.info(
            "mcp_approval_requested",
            run_id=run.id,
            tool_id=ask.tool_id,
            caller=ask.caller.value,
            window_seconds=window,
        )
        # FORGE-465: the client learns which approval it is waiting on now,
        # not when the window closes.
        await notify_held(ask, run.id)

        wait = await await_approval_decision(
            store,
            run.id,
            timeout_seconds=window,
            poll_interval=poll_interval,
            sleep=asyncio.sleep,
        )
        outcome = _OUTCOMES[wait]

        # Who answered is read back off the ledger rather than passed around,
        # because the ledger is what a reviewer and an auditor both look at.
        # A timeout has no approver by construction: nobody answered.
        decided = store.get(run.id)
        if outcome is ApprovalOutcome.TIMED_OUT and decided.status is RunStatus.CANCELED:
            # Closed by something other than this wait's own window.
            outcome = ApprovalOutcome.CANCELLED
        approver: Approver | None = None
        if decided.approved_by:
            approver = Approver(
                actor_id=decided.approved_by,
                verified=decided.approver_verified,
            )

        logger.info(
            "mcp_approval_resolved",
            run_id=run.id,
            tool_id=ask.tool_id,
            outcome=outcome.value,
            approved_by=approver.actor_id if approver else None,
            approver_verified=approver.verified if approver else None,
        )
        return ApprovalResolution(outcome=outcome, approver=approver, approval_id=run.id)

    return gate


class InProcessApprovalLedger:
    """Inline (elicitation) holds written to this process's own ledger (FORGE-473).

    For an MCP server running inside the gateway, where the approval store is
    reachable directly. A sidecar uses
    :class:`metaforge.mcp.remote_approvals.RemoteApprovalLedger` instead.
    """

    async def open_hold(self, ask: ApprovalAsk, *, route: str) -> str:
        store = get_approval_store()
        try:
            run = store.create(
                {
                    "tool": ask.tool_id,
                    "arguments": ask.arguments,
                    "caller": ask.caller.value,
                    "source": "mcp",
                    "session_id": ask.session_id,
                    "project": ask.project,
                    "route": route,
                    "client": ask.client,
                }
            )
            store.start(run.id)
            window = ask.timeout_seconds or DEFAULT_TIMEOUT_SECONDS
            store.request_approval(
                run.id,
                reason=ask.reason,
                deadline=store.now() + window + HOLD_DEADLINE_GRACE_SECONDS,
            )
        except Exception as exc:  # noqa: BLE001 - reported as a refusal
            raise ApprovalLedgerUnavailableError(
                ask.tool_id, str(exc) or type(exc).__name__
            ) from exc
        return run.id

    async def close_hold(
        self,
        approval_id: str,
        outcome: ApprovalOutcome,
        *,
        route: str,
        approver: Approver | None,
        reason: str | None = None,
    ) -> ApprovalOutcome:
        store = get_approval_store()
        run = store.get(approval_id)
        if run.status is RunStatus.AWAITING_APPROVAL:
            if outcome in (ApprovalOutcome.APPROVED, ApprovalOutcome.REJECTED):
                run = record_inline_answer(
                    approval_id,
                    approved=outcome is ApprovalOutcome.APPROVED,
                    approver=approver.actor_id if approver else None,
                    verified=approver.verified if approver else False,
                )
            elif outcome is ApprovalOutcome.TIMED_OUT:
                run = store.time_out(approval_id, reason=reason or "the prompt went unanswered")
            else:
                run = store.cancel(approval_id, reason=reason or "the prompt was dismissed")
        # Whatever is on record wins: a hold already closed elsewhere is read
        # back rather than overwritten.
        if run.status in (RunStatus.RUNNING, RunStatus.COMPLETED):
            return ApprovalOutcome.APPROVED
        if run.status is RunStatus.TIMED_OUT:
            return ApprovalOutcome.TIMED_OUT
        if run.status is RunStatus.CANCELED:
            return ApprovalOutcome.CANCELLED
        return ApprovalOutcome.REJECTED
