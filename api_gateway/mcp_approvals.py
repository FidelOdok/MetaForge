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

from api_gateway.chat.tool_approvals import get_approval_store
from mcp_core.guardrails import ApprovalAsk, ApprovalGateFn, ApprovalOutcome
from orchestrator.harness.runs import ApprovalWait, await_approval_decision

logger = structlog.get_logger(__name__)

#: How long a held MCP call waits before it is denied. Shorter than a chat
#: approval on purpose: an MCP client is holding a request open, and most
#: will give up on their own well before a generous server-side window
#: expires — at which point the human's click lands on a call nobody is
#: waiting for any more.
DEFAULT_TIMEOUT_SECONDS = 180.0
DEFAULT_POLL_INTERVAL = 0.5

_OUTCOMES: dict[ApprovalWait, ApprovalOutcome] = {
    ApprovalWait.APPROVED: ApprovalOutcome.APPROVED,
    ApprovalWait.REJECTED: ApprovalOutcome.REJECTED,
    ApprovalWait.TIMED_OUT: ApprovalOutcome.TIMED_OUT,
}


def build_mcp_approval_gate(
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
) -> ApprovalGateFn:
    """An approval gate the MCP server can be constructed with.

    Returns a callable, not a class, because that is the whole seam: the
    MCP server must not import this module, and anything it does import from
    the gateway layer is a layering violation waiting to be reintroduced.
    """

    async def gate(ask: ApprovalAsk) -> ApprovalOutcome:
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
            }
        )
        store.start(run.id)
        store.request_approval(run.id, reason=ask.reason)
        logger.info(
            "mcp_approval_requested",
            run_id=run.id,
            tool_id=ask.tool_id,
            caller=ask.caller.value,
        )

        wait = await await_approval_decision(
            store,
            run.id,
            timeout_seconds=timeout_seconds,
            poll_interval=poll_interval,
            sleep=asyncio.sleep,
        )
        outcome = _OUTCOMES[wait]
        logger.info(
            "mcp_approval_resolved",
            run_id=run.id,
            tool_id=ask.tool_id,
            outcome=outcome.value,
        )
        return outcome

    return gate
