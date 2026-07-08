"""Proposal recorder — bridges the twin MCP adapter to the approval workflow (MET-548).

The ``twin.propose_change`` tool lives in ``tool_registry`` (which must not import
``api_gateway``), so the gateway injects this async callable — like the decision
/ geometry recorders. It files a reviewable ``DesignChangeProposal`` on the same
``ApprovalWorkflow`` the ``/v1/assistant/proposals`` routes + ApprovalsPage use,
so an agent-proposed change shows up for human approval (HITL).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

import structlog

from api_gateway.assistant.approval import ApprovalWorkflow

logger = structlog.get_logger(__name__)

ProposalRecorder = Callable[..., Awaitable[dict[str, Any]]]


def _to_uuid(value: str | None) -> UUID | None:
    if not value:
        return None
    try:
        return UUID(str(value))
    except (ValueError, AttributeError):
        return None


def make_proposal_recorder(workflow: ApprovalWorkflow) -> ProposalRecorder:
    """Return an async ``propose(...)`` that files a pending proposal."""

    async def propose(
        *,
        agent_code: str | None,
        description: str,
        diff: dict[str, Any],
        work_products: list[str] | None = None,
        project_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        wps = [u for u in (_to_uuid(w) for w in (work_products or [])) if u is not None]
        proposal = await workflow.propose_change(
            agent_code=agent_code or "assistant",
            description=description,
            diff=diff,
            work_products=wps,
            session_id=_to_uuid(session_id),
            project_id=project_id,
        )
        logger.info("proposal_recorded_via_tool", change_id=str(proposal.change_id))
        return {
            "change_id": str(proposal.change_id),
            "status": getattr(proposal.status, "value", str(proposal.status)),
        }

    return propose


def make_http_proposal_recorder(gateway_url: str) -> ProposalRecorder:
    """Return an async ``propose(...)`` that forwards to the gateway over HTTP.

    Used by the out-of-process MCP sidecar (MET-552): the sidecar has no access
    to the gateway's in-memory ``ApprovalWorkflow``, so it POSTs proposals to
    ``POST /v1/assistant/proposals`` — the single place the dashboard
    ``/approvals`` + in-twin card read from. Best-effort: a forwarding failure
    is logged and returned as an error payload, never raised, so the underlying
    ``twin.propose_change`` tool call doesn't hard-fail.
    """
    base = gateway_url.rstrip("/")

    async def propose(
        *,
        agent_code: str | None,
        description: str,
        diff: dict[str, Any],
        work_products: list[str] | None = None,
        project_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        import httpx  # lazy — keep import side effects out of module load

        wps = [str(u) for u in (_to_uuid(w) for w in (work_products or [])) if u is not None]
        payload: dict[str, Any] = {
            "agent_code": agent_code or "assistant",
            "description": description,
            "diff": diff,
            "work_products_affected": wps,
            "project_id": project_id,
            "session_id": session_id,
        }
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(f"{base}/v1/assistant/proposals", json=payload)
                resp.raise_for_status()
                data = resp.json()
            logger.info("proposal_forwarded_to_gateway", change_id=data.get("change_id"))
            return {"change_id": data.get("change_id"), "status": data.get("status")}
        except Exception as exc:  # noqa: BLE001 — best-effort; never hard-fail the tool
            logger.warning("http_proposal_forward_failed", error=str(exc), gateway=base)
            return {"error": f"failed to file proposal: {exc}"}

    return propose
