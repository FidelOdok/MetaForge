"""Escalate spec-change design violations to engineer review (MET-455 Phase 3).

The last link in the L4-staleness chain: once ``DesignRevalidator`` has
found designs that started violating constraints after a spec change,
those designs must not silently ship — an engineer has to look. This
coordinator turns each violated design into an ``ApprovalRequest`` bound
to the engineer-review role and publishes an ``APPROVAL_REQUESTED`` event
so the gateway / approval workflow can surface it.

This is the deterministic, unit-testable core (mirroring how
``ConsolidationOrchestrator`` stays separate from its Temporal wrapper).
The Temporal approval gate that *waits* on the human decision is the
existing ``wait_for_approval`` activity — this module only raises the
flag; it does not block on the verdict.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

import structlog

from digital_twin.memory.validation.design_revalidator import (
    DesignRevalidationResult,
    DesignViolation,
)
from observability.tracing import get_tracer
from orchestrator.activities.base_activity import ApprovalRequest
from orchestrator.event_bus.events import Event, EventType

logger = structlog.get_logger(__name__)
tracer = get_tracer("orchestrator.workflows.design_escalation")

DEFAULT_ENGINEER_ROLE = "engineer"


class SupportsPublish(Protocol):
    """Minimal slice of the event bus the escalator needs."""

    async def publish(self, event: Event) -> None: ...


@dataclass(frozen=True)
class EscalationReport:
    """Audit trail of one escalation pass."""

    escalated_count: int = 0
    approval_requests: tuple[ApprovalRequest, ...] = field(default_factory=tuple)

    @property
    def escalated(self) -> bool:
        return self.escalated_count > 0


def _build_approval_request(
    violation: DesignViolation,
    *,
    run_id: str,
    step_id: str,
    required_role: str,
) -> ApprovalRequest:
    summaries = violation.violation_summaries
    detail = "; ".join(summaries) if summaries else "constraint violation after spec change"
    return ApprovalRequest(
        approval_id=str(uuid4()),
        description=(
            f"Design {violation.design_id} now violates constraints after a spec change: {detail}"
        ),
        required_role=required_role,
        work_product_ids=[str(violation.design_id)],
        run_id=run_id,
        step_id=step_id,
    )


class DesignEscalator:
    """Raise engineer-review approval gates for designs broken by a spec change."""

    def __init__(
        self,
        *,
        event_bus: SupportsPublish | None = None,
        required_role: str = DEFAULT_ENGINEER_ROLE,
        source: str = "orchestrator.design_escalation",
    ) -> None:
        self._event_bus = event_bus
        self._required_role = required_role
        self._source = source

    async def escalate(
        self,
        result: DesignRevalidationResult,
        *,
        run_id: str = "",
        step_id: str = "design_revalidation",
    ) -> EscalationReport:
        """Escalate every violated design in ``result`` for engineer review.

        Builds one ``ApprovalRequest`` per violated design and, when an
        event bus is wired, publishes a matching ``APPROVAL_REQUESTED``
        event. A clean result (no violations) is a no-op and returns an
        empty report.
        """
        if result.passed:
            return EscalationReport()

        with tracer.start_as_current_span("design_escalation.escalate") as span:
            span.set_attribute("escalation.violated_count", result.violated_count)
            requests: list[ApprovalRequest] = []
            for violation in result.violated:
                request = _build_approval_request(
                    violation,
                    run_id=run_id,
                    step_id=step_id,
                    required_role=self._required_role,
                )
                requests.append(request)
                if self._event_bus is not None:
                    await self._event_bus.publish(
                        Event(
                            id=str(uuid4()),
                            type=EventType.APPROVAL_REQUESTED,
                            timestamp=datetime.now(UTC).isoformat(),
                            source=self._source,
                            data={
                                "approval_id": request.approval_id,
                                "design_id": str(violation.design_id),
                                "required_role": self._required_role,
                                "violations": list(violation.violation_summaries),
                            },
                        )
                    )
                logger.info(
                    "design_escalated_for_review",
                    design_id=str(violation.design_id),
                    approval_id=request.approval_id,
                    required_role=self._required_role,
                    violation_count=len(violation.violations),
                )

            report = EscalationReport(
                escalated_count=len(requests),
                approval_requests=tuple(requests),
            )
            span.set_attribute("escalation.escalated_count", report.escalated_count)
            return report
