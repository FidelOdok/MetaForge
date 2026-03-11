"""Gate approval workflow — human-in-the-loop gate transitions.

Connects the gate engine readiness evaluation to an approval workflow,
ensuring gate transitions require explicit human approval before state
changes are committed.

Flow:
1. Agent/user requests gate transition (e.g., EVT -> DVT)
2. Gate engine evaluates readiness score
3. If score >= threshold -> create approval request with readiness report
4. If score < threshold -> reject with blocker list (no approval created)
5. Approver reviews report, approves/rejects with comments
6. Approved -> gate state updated, event emitted
7. Rejected -> feedback returned
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import structlog

from digital_twin.thread.gate_engine.engine import GateEngine
from digital_twin.thread.gate_engine.models import (
    GateApprovalResult,
    GateStage,
    GateTransitionRequest,
    GateTransitionStatus,
    ReadinessScore,
)
from observability.tracing import get_tracer
from orchestrator.event_bus.events import Event, EventType
from orchestrator.event_bus.subscribers import EventBus

logger = structlog.get_logger(__name__)
tracer = get_tracer("digital_twin.gate_engine.approval")


class GateApprovalService:
    """Orchestrates gate transitions through a human-in-the-loop approval workflow.

    Wraps the GateEngine to enforce that transitions are only committed
    after explicit approval. Provides override capability for authorized
    personnel with mandatory justification recorded in the audit trail.
    """

    def __init__(
        self,
        gate_engine: GateEngine,
        event_bus: EventBus,
    ) -> None:
        self._gate_engine = gate_engine
        self._event_bus = event_bus
        # Track pending approval requests: request_id -> (transition_id, readiness, request)
        self._pending: dict[UUID, _PendingApproval] = {}
        # Audit trail for all decisions
        self._audit_trail: list[GateApprovalResult] = []

    async def request_transition(
        self,
        project_id: str,
        from_gate: GateStage | None,
        to_gate: GateStage,
        requestor_id: str = "system",
        branch: str = "main",
    ) -> GateApprovalResult | _PendingApproval:
        """Evaluate readiness and create an approval request if passing.

        If the readiness score meets the threshold, a pending approval is
        created and a GATE_REQUESTED event is emitted. If the score is below
        threshold, the request is immediately rejected with a blocker list
        and no approval is created.

        Returns:
            _PendingApproval if readiness passes (approval pending).
            GateApprovalResult with REJECTED status if readiness fails.
        """
        with tracer.start_as_current_span("gate_approval.request_transition") as span:
            span.set_attribute("project_id", project_id)
            span.set_attribute("gate.from", str(from_gate) if from_gate else "None")
            span.set_attribute("gate.to", str(to_gate))
            span.set_attribute("requestor_id", requestor_id)

            # Evaluate readiness via the gate engine
            readiness = await self._gate_engine.evaluate_readiness(to_gate, branch)

            span.set_attribute("gate.ready", readiness.ready)
            span.set_attribute("gate.overall_score", readiness.overall_score)

            request = GateTransitionRequest(
                project_id=project_id,
                from_gate=from_gate,
                to_gate=to_gate,
                requestor_id=requestor_id,
                branch=branch,
            )

            if not readiness.ready:
                # Score below threshold — reject immediately, no approval created
                result = GateApprovalResult(
                    request_id=uuid4(),
                    status=GateTransitionStatus.REJECTED,
                    approver_id="system",
                    comment=f"Readiness check failed: {'; '.join(readiness.blockers)}",
                    readiness_score=readiness,
                    decided_at=datetime.now(UTC),
                )
                self._audit_trail.append(result)

                logger.info(
                    "gate_transition_blocked",
                    project_id=project_id,
                    to_gate=str(to_gate),
                    overall_score=readiness.overall_score,
                    blocker_count=len(readiness.blockers),
                )

                return result

            # Readiness passes — create pending approval
            request_id = uuid4()
            pending = _PendingApproval(
                request_id=request_id,
                request=request,
                readiness=readiness,
                created_at=datetime.now(UTC),
            )
            self._pending[request_id] = pending

            # Emit GATE_REQUESTED event
            await self._emit_event(
                EventType.GATE_REQUESTED,
                request_id=request_id,
                project_id=project_id,
                from_gate=from_gate,
                to_gate=to_gate,
                readiness=readiness,
                requestor_id=requestor_id,
            )

            logger.info(
                "gate_transition_requested",
                request_id=str(request_id),
                project_id=project_id,
                to_gate=str(to_gate),
                overall_score=readiness.overall_score,
            )

            return pending

    async def approve(
        self,
        request_id: UUID,
        approver_id: str,
        comment: str = "",
    ) -> GateApprovalResult:
        """Approve a pending gate transition.

        Transitions the gate state, emits GATE_APPROVED, and records
        the decision in the audit trail.

        Raises:
            ValueError: If request_id not found or not pending.
        """
        with tracer.start_as_current_span("gate_approval.approve") as span:
            span.set_attribute("request_id", str(request_id))
            span.set_attribute("approver_id", approver_id)

            pending = self._pending.get(request_id)
            if pending is None:
                raise ValueError(f"Approval request {request_id} not found")

            now = datetime.now(UTC)

            # Perform the actual gate transition via the engine
            transition = await self._gate_engine.request_transition(
                pending.request.to_gate,
                pending.request.branch,
                approver_id,
            )
            await self._gate_engine.approve_transition(transition.id, approver_id, comment)

            result = GateApprovalResult(
                request_id=request_id,
                status=GateTransitionStatus.APPROVED,
                approver_id=approver_id,
                comment=comment,
                readiness_score=pending.readiness,
                decided_at=now,
            )
            self._audit_trail.append(result)
            del self._pending[request_id]

            # Emit GATE_APPROVED event
            await self._emit_event(
                EventType.GATE_APPROVED,
                request_id=request_id,
                project_id=pending.request.project_id,
                from_gate=pending.request.from_gate,
                to_gate=pending.request.to_gate,
                readiness=pending.readiness,
                approver_id=approver_id,
                comment=comment,
            )

            logger.info(
                "gate_transition_approved",
                request_id=str(request_id),
                approver_id=approver_id,
                to_gate=str(pending.request.to_gate),
            )

            return result

    async def reject(
        self,
        request_id: UUID,
        approver_id: str,
        comment: str = "",
    ) -> GateApprovalResult:
        """Reject a pending gate transition.

        Records the rejection in the audit trail and emits GATE_REJECTED.

        Raises:
            ValueError: If request_id not found or not pending.
        """
        with tracer.start_as_current_span("gate_approval.reject") as span:
            span.set_attribute("request_id", str(request_id))
            span.set_attribute("approver_id", approver_id)

            pending = self._pending.get(request_id)
            if pending is None:
                raise ValueError(f"Approval request {request_id} not found")

            now = datetime.now(UTC)

            result = GateApprovalResult(
                request_id=request_id,
                status=GateTransitionStatus.REJECTED,
                approver_id=approver_id,
                comment=comment,
                readiness_score=pending.readiness,
                decided_at=now,
            )
            self._audit_trail.append(result)
            del self._pending[request_id]

            # Emit GATE_REJECTED event
            await self._emit_event(
                EventType.GATE_REJECTED,
                request_id=request_id,
                project_id=pending.request.project_id,
                from_gate=pending.request.from_gate,
                to_gate=pending.request.to_gate,
                readiness=pending.readiness,
                approver_id=approver_id,
                comment=comment,
            )

            logger.info(
                "gate_transition_rejected",
                request_id=str(request_id),
                approver_id=approver_id,
                to_gate=str(pending.request.to_gate),
            )

            return result

    async def override(
        self,
        request_id: UUID,
        approver_id: str,
        justification: str,
    ) -> GateApprovalResult:
        """Override a blocked transition despite failing readiness.

        Requires a justification that is recorded in the audit trail.
        This allows authorized personnel to force a gate transition
        when blockers are acknowledged but deemed acceptable.

        Note: This can also be used on pending approvals (passing readiness)
        if the approver wants to record a justification.

        Raises:
            ValueError: If request_id not found, not pending, or no justification.
        """
        with tracer.start_as_current_span("gate_approval.override") as span:
            span.set_attribute("request_id", str(request_id))
            span.set_attribute("approver_id", approver_id)

            if not justification.strip():
                raise ValueError("Override requires a non-empty justification")

            pending = self._pending.get(request_id)
            if pending is None:
                raise ValueError(f"Approval request {request_id} not found")

            now = datetime.now(UTC)

            # Force the gate transition via the engine
            transition = await self._gate_engine.request_transition(
                pending.request.to_gate,
                pending.request.branch,
                approver_id,
            )
            await self._gate_engine.approve_transition(
                transition.id, approver_id, f"OVERRIDE: {justification}"
            )

            result = GateApprovalResult(
                request_id=request_id,
                status=GateTransitionStatus.OVERRIDDEN,
                approver_id=approver_id,
                comment=f"OVERRIDE: {justification}",
                readiness_score=pending.readiness,
                override_justification=justification,
                decided_at=now,
            )
            self._audit_trail.append(result)
            del self._pending[request_id]

            # Emit GATE_APPROVED event (override is a form of approval)
            await self._emit_event(
                EventType.GATE_APPROVED,
                request_id=request_id,
                project_id=pending.request.project_id,
                from_gate=pending.request.from_gate,
                to_gate=pending.request.to_gate,
                readiness=pending.readiness,
                approver_id=approver_id,
                comment=f"OVERRIDE: {justification}",
                override=True,
                justification=justification,
            )

            logger.info(
                "gate_transition_overridden",
                request_id=str(request_id),
                approver_id=approver_id,
                to_gate=str(pending.request.to_gate),
                justification=justification,
            )

            return result

    def get_pending_approvals(self) -> list[_PendingApproval]:
        """Return all pending approval requests."""
        return list(self._pending.values())

    def get_audit_trail(self) -> list[GateApprovalResult]:
        """Return the full audit trail of approval decisions."""
        return list(self._audit_trail)

    # ── Private helpers ────────────────────────────────────────────────

    async def _emit_event(
        self,
        event_type: EventType,
        *,
        request_id: UUID,
        project_id: str,
        from_gate: GateStage | None,
        to_gate: GateStage,
        readiness: ReadinessScore,
        approver_id: str = "",
        requestor_id: str = "",
        comment: str = "",
        override: bool = False,
        justification: str = "",
    ) -> None:
        """Emit a gate approval event on the event bus."""
        event = Event(
            id=str(uuid4()),
            type=event_type,
            timestamp=datetime.now(UTC).isoformat(),
            source="gate_approval_service",
            data={
                "request_id": str(request_id),
                "project_id": project_id,
                "from_gate": str(from_gate) if from_gate else None,
                "to_gate": str(to_gate),
                "overall_score": readiness.overall_score,
                "ready": readiness.ready,
                "blocker_count": len(readiness.blockers),
                "blockers": readiness.blockers,
                "approver_id": approver_id,
                "requestor_id": requestor_id,
                "comment": comment,
                "override": override,
                "justification": justification,
            },
        )
        await self._event_bus.publish(event)


class _PendingApproval:
    """Internal tracker for a pending gate approval request."""

    __slots__ = ("request_id", "request", "readiness", "created_at")

    def __init__(
        self,
        request_id: UUID,
        request: GateTransitionRequest,
        readiness: ReadinessScore,
        created_at: datetime,
    ) -> None:
        self.request_id = request_id
        self.request = request
        self.readiness = readiness
        self.created_at = created_at
