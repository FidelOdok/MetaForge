"""Holding a write in the gateway's ledger, from another process (FORGE-406).

``api_gateway.mcp_approvals.build_mcp_approval_gate`` parks a call in a
process-level ``InMemoryRunStore``. That works when the MCP server runs
*inside* the gateway. The HTTP sidecar is a separate process, so a call held
there would sit in a queue the dashboard — served by the gateway — cannot
see, and nobody would ever answer it.

Worse, until FORGE-406 nothing built that gate at all outside tests. So
``approval_gate`` was ``None`` and **every write from a plugin was refused**
with "no approval gate is configured". The guardrail from FORGE-359 was
present, correct, and unreachable.

This gate posts the held call to the gateway and polls for the decision, so
there is exactly one ledger and the dashboard shows everything. A second
store would have been the more obvious fix and the wrong one: two queues
means a reviewer clearing one while the other fills.

The approver comes back off the ledger entry, never from anything the caller
said (FORGE-393).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import structlog

from mcp_core.guardrails import (
    ApprovalAsk,
    ApprovalGateFn,
    ApprovalOutcome,
    ApprovalResolution,
    Approver,
)

logger = structlog.get_logger(__name__)

__all__ = ["DEFAULT_POLL_INTERVAL", "DEFAULT_TIMEOUT_SECONDS", "build_remote_approval_gate"]

#: Matches the in-process gate. Shorter than a chat approval on purpose: an
#: MCP client holds a request open, and most give up well before a generous
#: server-side window expires — at which point the human's click lands on a
#: call nobody is waiting for any more.
DEFAULT_TIMEOUT_SECONDS = 180.0
DEFAULT_POLL_INTERVAL = 1.0

_PENDING = "awaiting_approval"
_APPROVED_STATES = {"running", "completed"}
_REJECTED_STATES = {"rejected", "canceled", "failed"}


def build_remote_approval_gate(
    gateway_url: str,
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    client: httpx.AsyncClient | None = None,
) -> ApprovalGateFn:
    """An approval gate that parks calls in the gateway's ledger."""
    base = gateway_url.rstrip("/")
    endpoint = f"{base}/v1/chat/tool_approvals"

    async def gate(ask: ApprovalAsk) -> ApprovalResolution:
        owned = client is None
        http = client or httpx.AsyncClient(timeout=30.0)
        try:
            try:
                created = await http.post(
                    endpoint,
                    json={
                        "tool": ask.tool_id,
                        "arguments": ask.arguments,
                        "reason": ask.reason,
                        "caller": ask.caller.value,
                        "source": "mcp",
                        "session_id": ask.session_id,
                        "project": ask.project,
                    },
                )
                created.raise_for_status()
            except Exception as exc:  # noqa: BLE001 — a gate reports, never raises
                # Cannot reach the ledger. Treating that as "approved" would
                # run the write; treating it as rejected at least holds the
                # line, and the log says which it was so an operator can tell
                # a refusal from an outage.
                logger.error(
                    "mcp_remote_approval_unreachable",
                    tool_id=ask.tool_id,
                    endpoint=endpoint,
                    error=str(exc),
                )
                return ApprovalResolution(outcome=ApprovalOutcome.REJECTED)

            run_id = created.json()["id"]
            logger.info(
                "mcp_remote_approval_requested",
                run_id=run_id,
                tool_id=ask.tool_id,
                caller=ask.caller.value,
            )

            deadline = time.monotonic() + timeout_seconds
            while time.monotonic() < deadline:
                await asyncio.sleep(poll_interval)
                try:
                    current = await http.get(f"{endpoint}/{run_id}")
                    current.raise_for_status()
                except Exception as exc:  # noqa: BLE001
                    # A blip mid-poll is not a decision. Keep waiting until
                    # the window closes rather than converting a network
                    # hiccup into a refusal a human never gave.
                    logger.warning("mcp_remote_approval_poll_failed", run_id=run_id, error=str(exc))
                    continue

                body: dict[str, Any] = current.json()
                status = str(body.get("status") or "")
                if status == _PENDING:
                    continue

                approver: Approver | None = None
                if body.get("approved_by"):
                    approver = Approver(
                        actor_id=str(body["approved_by"]),
                        verified=bool(body.get("approver_verified")),
                    )
                outcome = (
                    ApprovalOutcome.APPROVED
                    if status in _APPROVED_STATES
                    else ApprovalOutcome.REJECTED
                    if status in _REJECTED_STATES
                    else ApprovalOutcome.REJECTED
                )
                logger.info(
                    "mcp_remote_approval_resolved",
                    run_id=run_id,
                    tool_id=ask.tool_id,
                    status=status,
                    outcome=outcome.value,
                    approved_by=approver.actor_id if approver else None,
                )
                # FORGE-417: the run id goes back with the answer, so the
                # caller can cite the ledger entry rather than assert a hold.
                return ApprovalResolution(outcome=outcome, approver=approver, approval_id=run_id)

            logger.info("mcp_remote_approval_timed_out", run_id=run_id, tool_id=ask.tool_id)
            # Distinct from rejected: nobody said no, nobody said anything.
            return ApprovalResolution(outcome=ApprovalOutcome.TIMED_OUT, approval_id=run_id)
        finally:
            if owned:
                await http.aclose()

    return gate
