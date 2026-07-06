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
