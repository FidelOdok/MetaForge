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

When this side stops waiting, for any reason, it closes the hold it opened
(FORGE-466): ``timed_out`` when the window closes, ``canceled`` when the call
is cancelled (a client that disconnected) or the wait fails. Left
``awaiting_approval``, the dashboard offered a button for a call nobody was
waiting for, and pressing it recorded an approval for a dead call. Closing is
best-effort: a failure is logged and counted, never raised into the tool
call. The gateway also expires a hold past the deadline this side sent, for
the case where this process dies without saying anything.
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
    ApprovalLedgerUnavailableError,
    ApprovalOutcome,
    ApprovalResolution,
    Approver,
    effective_hold_window,
    notify_held,
)
from observability.metrics import MetricsCollector, collector_for

logger = structlog.get_logger(__name__)

__all__ = [
    "DEFAULT_POLL_INTERVAL",
    "DEFAULT_TIMEOUT_SECONDS",
    "RemoteApprovalLedger",
    "build_remote_approval_gate",
]

#: Matches the in-process gate. Shorter than a chat approval on purpose: an
#: MCP client holds a request open, and most give up well before a generous
#: server-side window expires — at which point the human's click lands on a
#: call nobody is waiting for any more.
#:
#: Used only when the server did not choose a window on the ask (FORGE-465:
#: it normally does, shorter when the client cannot be sent progress).
DEFAULT_TIMEOUT_SECONDS = 180.0
DEFAULT_POLL_INTERVAL = 1.0
#: How long closing a hold may take before it is given up on. The tool call
#: is already over by then; this only bounds how long it lingers.
RESOLVE_TIMEOUT_SECONDS = 5.0

_PENDING = "awaiting_approval"
_APPROVED_STATES = {"running", "completed"}
_REJECTED_STATES = {"rejected", "canceled", "failed", "timed_out"}

#: Closes started while the call was being cancelled. Held here so the task
#: is not garbage-collected before it finishes.
_background: set[asyncio.Task[Any]] = set()


def build_remote_approval_gate(
    gateway_url: str,
    *,
    timeout_seconds: float | None = None,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    client: httpx.AsyncClient | None = None,
    metrics: MetricsCollector | None = None,
) -> ApprovalGateFn:
    """An approval gate that parks calls in the gateway's ledger.

    ``timeout_seconds``, when given, caps the window the server asks for
    (FORGE-465); left out, the ask's window applies.
    """
    base = gateway_url.rstrip("/")
    endpoint = f"{base}/v1/chat/tool_approvals"
    collector: list[MetricsCollector] = [metrics] if metrics is not None else []

    def _metrics() -> MetricsCollector:
        if not collector:
            collector.append(collector_for("metaforge-mcp"))
        return collector[0]

    async def resolve(http: httpx.AsyncClient, run_id: str, outcome: str, reason: str) -> str:
        """Close the hold. Returns resolved, already_resolved, decided or failed."""
        try:
            response = await asyncio.wait_for(
                http.post(
                    f"{endpoint}/{run_id}/resolve",
                    json={"outcome": outcome, "reason": reason},
                ),
                timeout=RESOLVE_TIMEOUT_SECONDS,
            )
            if response.status_code == 409:
                result = "decided"
            else:
                response.raise_for_status()
                result = (
                    "resolved" if response.json().get("status") == outcome else "already_resolved"
                )
        except Exception as exc:  # noqa: BLE001 - closing is best-effort
            # The gateway's own deadline will expire the hold; say so loudly
            # here so an operator can tell why it lingered until then.
            logger.warning(
                "mcp_remote_approval_resolve_failed",
                run_id=run_id,
                outcome=outcome,
                error=str(exc) or type(exc).__name__,
            )
            result = "failed"
        else:
            logger.info(
                "mcp_remote_approval_hold_closed", run_id=run_id, outcome=outcome, result=result
            )
        try:
            _metrics().record_tool_approval_resolution(outcome, "waiter", result)
        except Exception:  # noqa: BLE001, S110 - a metric must not fail the call
            pass
        return result

    def decision_from(body: dict[str, Any], run_id: str) -> ApprovalResolution:
        status = str(body.get("status") or "")
        approver: Approver | None = None
        if body.get("approved_by"):
            approver = Approver(
                actor_id=str(body["approved_by"]),
                verified=bool(body.get("approver_verified")),
            )
        outcome = (
            ApprovalOutcome.APPROVED
            if status in _APPROVED_STATES
            else ApprovalOutcome.TIMED_OUT
            if status == "timed_out"
            else ApprovalOutcome.CANCELLED
            if status == "canceled"
            else ApprovalOutcome.REJECTED
        )
        # FORGE-417: the run id goes back with the answer, so the caller can
        # cite the ledger entry rather than assert a hold.
        return ApprovalResolution(outcome=outcome, approver=approver, approval_id=run_id)

    async def gate(ask: ApprovalAsk) -> ApprovalResolution:
        timeout = effective_hold_window(timeout_seconds, ask, DEFAULT_TIMEOUT_SECONDS)
        owned = client is None
        http = client or httpx.AsyncClient(timeout=30.0)
        handed_off = False
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
                        "route": "dashboard",
                        "client": ask.client,
                        # FORGE-466: the gateway expires the hold past this
                        # window if this side never closes it.
                        "timeout_seconds": timeout,
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
                window_seconds=timeout,
            )
            # FORGE-465: the client learns which approval it is waiting on
            # now, not when the window closes.
            await notify_held(ask, run_id)

            try:
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    await asyncio.sleep(poll_interval)
                    try:
                        current = await http.get(f"{endpoint}/{run_id}")
                        current.raise_for_status()
                    except Exception as exc:  # noqa: BLE001
                        # A blip mid-poll is not a decision. Keep waiting
                        # until the window closes rather than converting a
                        # network hiccup into a refusal a human never gave.
                        logger.warning(
                            "mcp_remote_approval_poll_failed", run_id=run_id, error=str(exc)
                        )
                        continue

                    body: dict[str, Any] = current.json()
                    if str(body.get("status") or "") == _PENDING:
                        continue
                    resolution = decision_from(body, run_id)
                    logger.info(
                        "mcp_remote_approval_resolved",
                        run_id=run_id,
                        tool_id=ask.tool_id,
                        status=body.get("status"),
                        outcome=resolution.outcome.value,
                        approved_by=resolution.approver.actor_id if resolution.approver else None,
                    )
                    return resolution
            except asyncio.CancelledError:
                # The call is being torn down: a client that disconnected, or
                # a server shutting down. Close the hold in a task of its own
                # and shield it, because a cancelled scope may refuse every
                # further await in this one.
                logger.info("mcp_remote_approval_cancelled", run_id=run_id, tool_id=ask.tool_id)
                closing = asyncio.ensure_future(
                    _close_then_release(
                        resolve(http, run_id, "canceled", "the waiting call was cancelled"),
                        http if owned else None,
                    )
                )
                _background.add(closing)
                closing.add_done_callback(_background.discard)
                handed_off = True
                try:
                    await asyncio.shield(closing)
                except BaseException:  # noqa: BLE001, S110 - re-raised below
                    pass
                raise
            except Exception as exc:  # noqa: BLE001 - a gate reports, never raises
                logger.error("mcp_remote_approval_wait_failed", run_id=run_id, error=str(exc))
                await resolve(http, run_id, "canceled", f"the wait failed: {exc}")
                return ApprovalResolution(outcome=ApprovalOutcome.REJECTED, approval_id=run_id)

            logger.info("mcp_remote_approval_timed_out", run_id=run_id, tool_id=ask.tool_id)
            result = await resolve(http, run_id, "timed_out", "the waiting call timed out")
            if result == "decided":
                # A decision landed between the last poll and the close. It
                # is honoured rather than clobbered, as the in-process wait
                # does: a human did answer.
                try:
                    current = await http.get(f"{endpoint}/{run_id}")
                    current.raise_for_status()
                    return decision_from(current.json(), run_id)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("mcp_remote_approval_poll_failed", run_id=run_id, error=str(exc))
            # Distinct from rejected: nobody said no, nobody said anything.
            return ApprovalResolution(outcome=ApprovalOutcome.TIMED_OUT, approval_id=run_id)
        finally:
            if owned and not handed_off:
                await http.aclose()

    return gate


async def _close_then_release(closing: Any, http: httpx.AsyncClient | None) -> None:
    """Run a close, then shut the client it used if this gate owned it."""
    try:
        await closing
    finally:
        if http is not None:
            await http.aclose()


#: Outcome names the gateway's resolve route takes (FORGE-473).
_LEDGER_OUTCOMES = {
    ApprovalOutcome.APPROVED: "approved",
    ApprovalOutcome.REJECTED: "rejected",
    ApprovalOutcome.TIMED_OUT: "timed_out",
    ApprovalOutcome.CANCELLED: "canceled",
}
_STATUS_OUTCOMES = {
    "running": ApprovalOutcome.APPROVED,
    "completed": ApprovalOutcome.APPROVED,
    "rejected": ApprovalOutcome.REJECTED,
    "timed_out": ApprovalOutcome.TIMED_OUT,
    "canceled": ApprovalOutcome.CANCELLED,
    "failed": ApprovalOutcome.REJECTED,
}


class RemoteApprovalLedger:
    """Writes inline (elicitation) holds into the gateway's ledger (FORGE-473).

    The same ledger :func:`build_remote_approval_gate` uses, so a call answered
    in the client's own prompt has an approval id, a route, a requester and an
    approver, and shows up beside the dashboard ones.
    """

    def __init__(
        self,
        gateway_url: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = RESOLVE_TIMEOUT_SECONDS,
    ) -> None:
        self._endpoint = f"{gateway_url.rstrip('/')}/v1/chat/tool_approvals"
        self._client = client
        self._timeout = timeout

    async def _post(self, url: str, body: dict[str, Any]) -> httpx.Response:
        if self._client is not None:
            return await self._client.post(url, json=body, timeout=self._timeout)
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            return await http.post(url, json=body)

    async def open_hold(self, ask: ApprovalAsk, *, route: str) -> str:
        try:
            created = await self._post(
                self._endpoint,
                {
                    "tool": ask.tool_id,
                    "arguments": ask.arguments,
                    "reason": ask.reason,
                    "caller": ask.caller.value,
                    "source": "mcp",
                    "session_id": ask.session_id,
                    "project": ask.project,
                    "route": route,
                    "client": ask.client,
                    "timeout_seconds": ask.timeout_seconds,
                },
            )
            created.raise_for_status()
            approval_id = str(created.json()["id"])
        except Exception as exc:  # noqa: BLE001 - reported as a refusal, never a pass
            logger.error(
                "mcp_inline_ledger_unreachable",
                tool_id=ask.tool_id,
                endpoint=self._endpoint,
                error=str(exc) or type(exc).__name__,
            )
            raise ApprovalLedgerUnavailableError(
                ask.tool_id, str(exc) or type(exc).__name__
            ) from exc
        logger.info(
            "mcp_inline_ledger_opened", run_id=approval_id, tool_id=ask.tool_id, route=route
        )
        return approval_id

    async def close_hold(
        self,
        approval_id: str,
        outcome: ApprovalOutcome,
        *,
        route: str,
        approver: Approver | None,
        reason: str | None = None,
    ) -> ApprovalOutcome:
        body: dict[str, Any] = {"outcome": _LEDGER_OUTCOMES[outcome], "reason": reason}
        if approver is not None:
            body["approver"] = approver.actor_id
            body["approver_verified"] = approver.verified
        response = await self._post(f"{self._endpoint}/{approval_id}/resolve", body)
        if response.status_code == 409:
            # Decided elsewhere first. Read what is on record and act on it.
            async with httpx.AsyncClient(timeout=self._timeout) as http:
                current = await http.get(f"{self._endpoint}/{approval_id}")
            current.raise_for_status()
            return _STATUS_OUTCOMES.get(str(current.json().get("status")), ApprovalOutcome.REJECTED)
        response.raise_for_status()
        recorded = _STATUS_OUTCOMES.get(str(response.json().get("status")))
        logger.info(
            "mcp_inline_ledger_closed", run_id=approval_id, outcome=outcome.value, route=route
        )
        return recorded or outcome
