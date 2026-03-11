"""Human-in-the-loop approval workflow for design changes.

Agents propose changes to the Digital Twin via ``ApprovalWorkflow``.
Each proposal is stored in-memory until a human reviewer approves or
rejects it.  WebSocket events are emitted at each lifecycle transition
so connected IDE assistants receive real-time updates.

Production will swap the in-memory store for a persistent backend.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import structlog

from api_gateway.assistant.schemas import (
    ApprovalDecisionType,
    ChangeStatus,
    DesignChangeProposal,
    EventType,
    WebSocketEvent,
)

logger = structlog.get_logger(__name__)


class ApprovalWorkflow:
    """Manages the lifecycle of design-change proposals.

    All state is held in a plain dict keyed by ``change_id``.  Event
    listeners (WebSocket connections) are registered per session and
    receive ``WebSocketEvent`` objects whenever a proposal transitions.
    """

    def __init__(self) -> None:
        self._proposals: dict[UUID, DesignChangeProposal] = {}
        # session_id -> list of asyncio.Queue for broadcasting events
        self._listeners: dict[UUID, list[asyncio.Queue[WebSocketEvent]]] = {}

    # ------------------------------------------------------------------
    # Event broadcasting
    # ------------------------------------------------------------------

    def subscribe(self, session_id: UUID) -> asyncio.Queue[WebSocketEvent]:
        """Register a new listener for *session_id* and return its queue."""
        queue: asyncio.Queue[WebSocketEvent] = asyncio.Queue()
        self._listeners.setdefault(session_id, []).append(queue)
        return queue

    def unsubscribe(self, session_id: UUID, queue: asyncio.Queue[WebSocketEvent]) -> None:
        """Remove a previously registered listener queue."""
        queues = self._listeners.get(session_id, [])
        if queue in queues:
            queues.remove(queue)

    async def _emit(self, event: WebSocketEvent) -> None:
        """Push *event* to every listener registered for its session."""
        queues = self._listeners.get(event.session_id, [])
        for q in queues:
            await q.put(event)
        logger.info(
            "event_emitted",
            event_type=event.event_type,
            session_id=str(event.session_id),
            listener_count=len(queues),
        )

    # ------------------------------------------------------------------
    # Proposal lifecycle
    # ------------------------------------------------------------------

    async def propose_change(
        self,
        agent_code: str,
        description: str,
        diff: dict[str, Any],
        artifacts: list[UUID],
        *,
        session_id: UUID | None = None,
        requires_approval: bool = True,
    ) -> DesignChangeProposal:
        """Create a new design-change proposal and notify listeners.

        Returns the created ``DesignChangeProposal``.
        """
        proposal = DesignChangeProposal(
            change_id=uuid4(),
            agent_code=agent_code,
            description=description,
            diff=diff,
            artifacts_affected=artifacts,
            requires_approval=requires_approval,
            session_id=session_id or uuid4(),
        )
        self._proposals[proposal.change_id] = proposal

        logger.info(
            "proposal_created",
            change_id=str(proposal.change_id),
            agent_code=agent_code,
            session_id=str(proposal.session_id),
        )

        await self._emit(
            WebSocketEvent(
                event_type=EventType.CHANGE_PROPOSED,
                payload={
                    "change_id": str(proposal.change_id),
                    "agent_code": agent_code,
                    "description": description,
                },
                session_id=proposal.session_id,
            )
        )

        return proposal

    def get_pending_proposals(self, session_id: UUID | None = None) -> list[DesignChangeProposal]:
        """Return proposals with ``status == pending``.

        If *session_id* is given, only proposals for that session are
        returned.
        """
        proposals = [p for p in self._proposals.values() if p.status == ChangeStatus.PENDING]
        if session_id is not None:
            proposals = [p for p in proposals if p.session_id == session_id]
        return proposals

    def get_proposal(self, change_id: UUID) -> DesignChangeProposal | None:
        """Return a single proposal by *change_id*, or ``None``."""
        return self._proposals.get(change_id)

    async def decide(
        self,
        change_id: UUID,
        decision: ApprovalDecisionType,
        reason: str,
        reviewer: str,
    ) -> DesignChangeProposal | None:
        """Record an approval or rejection for the given proposal.

        Returns the updated proposal, or ``None`` if *change_id* is
        unknown.  Only proposals in ``pending`` status can be decided.
        """
        proposal = self._proposals.get(change_id)
        if proposal is None:
            return None

        if proposal.status != ChangeStatus.PENDING:
            logger.warning(
                "decide_on_non_pending",
                change_id=str(change_id),
                current_status=proposal.status,
            )
            return proposal

        now = datetime.now(UTC)

        if decision == ApprovalDecisionType.APPROVE:
            proposal.status = ChangeStatus.APPROVED
            event_type = EventType.CHANGE_APPROVED
        else:
            proposal.status = ChangeStatus.REJECTED
            event_type = EventType.CHANGE_REJECTED

        proposal.decided_at = now
        proposal.decision_reason = reason
        proposal.reviewer = reviewer

        logger.info(
            "proposal_decided",
            change_id=str(change_id),
            decision=decision,
            reviewer=reviewer,
        )

        await self._emit(
            WebSocketEvent(
                event_type=event_type,
                payload={
                    "change_id": str(change_id),
                    "decision": decision,
                    "reason": reason,
                    "reviewer": reviewer,
                },
                session_id=proposal.session_id,
            )
        )

        return proposal

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @property
    def proposal_count(self) -> int:
        """Total number of proposals (all statuses)."""
        return len(self._proposals)

    def clear(self) -> None:
        """Remove all proposals and listeners (useful for tests)."""
        self._proposals.clear()
        self._listeners.clear()
