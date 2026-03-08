"""Gate Engine — EVT/DVT/PVT readiness evaluation and transition management.

Orchestrates multi-factor readiness scoring for hardware development gates,
manages transition requests, and emits events on gate progression.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import structlog

from digital_twin.thread.gate_engine.models import (
    CriterionResult,
    GateCriterion,
    GateCriterionType,
    GateDefinition,
    GateStage,
    GateTransition,
    GateTransitionStatus,
    ReadinessScore,
)
from digital_twin.thread.gate_engine.scoring import (
    calculate_bom_risk,
    calculate_constraint_compliance,
    calculate_design_review,
    calculate_requirement_coverage,
    calculate_test_evidence,
)
from observability.tracing import get_tracer
from orchestrator.event_bus.events import Event, EventType
from orchestrator.event_bus.subscribers import EventBus
from twin_core.api import TwinAPI
from twin_core.constraint_engine.validator import ConstraintEngine

logger = structlog.get_logger(__name__)
tracer = get_tracer("digital_twin.gate_engine")


# ---------------------------------------------------------------------------
# Default gate definitions
# ---------------------------------------------------------------------------

_EVT_DEFINITION = GateDefinition(
    stage=GateStage.EVT,
    min_overall_score=60.0,
    criteria=[
        GateCriterion(
            type=GateCriterionType.REQUIREMENT_COVERAGE,
            name="Requirement Coverage",
            description="Percentage of requirements with linked test evidence",
            weight=0.25,
            threshold=50.0,
            required=True,
        ),
        GateCriterion(
            type=GateCriterionType.CONSTRAINT_COMPLIANCE,
            name="Constraint Compliance",
            description="Percentage of constraints passing evaluation",
            weight=0.30,
            threshold=70.0,
            required=True,
        ),
        GateCriterion(
            type=GateCriterionType.BOM_RISK,
            name="BOM Risk",
            description="Inverse of average BOM item risk score",
            weight=0.15,
            threshold=50.0,
            required=False,
        ),
        GateCriterion(
            type=GateCriterionType.TEST_EVIDENCE,
            name="Test Evidence",
            description="Percentage of test plans with results",
            weight=0.20,
            threshold=40.0,
            required=False,
        ),
        GateCriterion(
            type=GateCriterionType.DESIGN_REVIEW,
            name="Design Review",
            description="Percentage of artifacts with review approval",
            weight=0.10,
            threshold=30.0,
            required=False,
        ),
    ],
)

_DVT_DEFINITION = GateDefinition(
    stage=GateStage.DVT,
    min_overall_score=75.0,
    criteria=[
        GateCriterion(
            type=GateCriterionType.REQUIREMENT_COVERAGE,
            name="Requirement Coverage",
            description="Percentage of requirements with linked test evidence",
            weight=0.25,
            threshold=70.0,
            required=True,
        ),
        GateCriterion(
            type=GateCriterionType.CONSTRAINT_COMPLIANCE,
            name="Constraint Compliance",
            description="Percentage of constraints passing evaluation",
            weight=0.25,
            threshold=85.0,
            required=True,
        ),
        GateCriterion(
            type=GateCriterionType.BOM_RISK,
            name="BOM Risk",
            description="Inverse of average BOM item risk score",
            weight=0.20,
            threshold=65.0,
            required=True,
        ),
        GateCriterion(
            type=GateCriterionType.TEST_EVIDENCE,
            name="Test Evidence",
            description="Percentage of test plans with results",
            weight=0.20,
            threshold=60.0,
            required=True,
        ),
        GateCriterion(
            type=GateCriterionType.DESIGN_REVIEW,
            name="Design Review",
            description="Percentage of artifacts with review approval",
            weight=0.10,
            threshold=50.0,
            required=False,
        ),
    ],
)

_PVT_DEFINITION = GateDefinition(
    stage=GateStage.PVT,
    min_overall_score=90.0,
    criteria=[
        GateCriterion(
            type=GateCriterionType.REQUIREMENT_COVERAGE,
            name="Requirement Coverage",
            description="Percentage of requirements with linked test evidence",
            weight=0.20,
            threshold=90.0,
            required=True,
        ),
        GateCriterion(
            type=GateCriterionType.CONSTRAINT_COMPLIANCE,
            name="Constraint Compliance",
            description="Percentage of constraints passing evaluation",
            weight=0.20,
            threshold=95.0,
            required=True,
        ),
        GateCriterion(
            type=GateCriterionType.BOM_RISK,
            name="BOM Risk",
            description="Inverse of average BOM item risk score",
            weight=0.20,
            threshold=80.0,
            required=True,
        ),
        GateCriterion(
            type=GateCriterionType.TEST_EVIDENCE,
            name="Test Evidence",
            description="Percentage of test plans with results",
            weight=0.25,
            threshold=85.0,
            required=True,
        ),
        GateCriterion(
            type=GateCriterionType.DESIGN_REVIEW,
            name="Design Review",
            description="Percentage of artifacts with review approval",
            weight=0.15,
            threshold=80.0,
            required=True,
        ),
    ],
)

DEFAULT_GATE_DEFINITIONS: dict[GateStage, GateDefinition] = {
    GateStage.EVT: _EVT_DEFINITION,
    GateStage.DVT: _DVT_DEFINITION,
    GateStage.PVT: _PVT_DEFINITION,
}


class GateEngine:
    """Manages EVT/DVT/PVT gate readiness evaluation and transitions.

    Evaluates multi-factor readiness scores against gate definitions,
    manages transition request lifecycle, and emits events on gate changes.
    """

    def __init__(
        self,
        twin: TwinAPI,
        constraint_engine: ConstraintEngine,
        event_bus: EventBus,
        gate_definitions: dict[GateStage, GateDefinition] | None = None,
    ) -> None:
        self._twin = twin
        self._constraint_engine = constraint_engine
        self._event_bus = event_bus
        self._definitions = gate_definitions or dict(DEFAULT_GATE_DEFINITIONS)
        self._transitions: dict[UUID, GateTransition] = {}
        self._current_stages: dict[str, GateStage] = {}  # branch -> current stage

    async def evaluate_readiness(
        self, stage: GateStage, branch: str = "main"
    ) -> ReadinessScore:
        """Evaluate all criteria for the given gate stage.

        Returns a ReadinessScore with individual criterion results,
        an overall weighted score, and a readiness determination.
        """
        with tracer.start_as_current_span("gate.evaluate_readiness") as span:
            span.set_attribute("gate.stage", str(stage))
            span.set_attribute("branch", branch)

            definition = self._definitions.get(stage)
            if definition is None:
                raise ValueError(f"No gate definition for stage {stage}")

            # Evaluate constraints once for use by scoring
            constraint_result = await self._constraint_engine.evaluate_all()

            # Score each criterion
            criteria_results: list[CriterionResult] = []
            for criterion in definition.criteria:
                score = await self._score_criterion(
                    criterion, branch, constraint_result
                )
                passed = score >= criterion.threshold
                blockers: list[str] = []
                if not passed and criterion.required:
                    blockers.append(
                        f"{criterion.name}: score {score:.1f} < threshold {criterion.threshold:.1f}"
                    )

                criteria_results.append(
                    CriterionResult(
                        criterion=criterion,
                        score=score,
                        passed=passed,
                        details=f"Score: {score:.1f}/{criterion.threshold:.1f}",
                        blockers=blockers,
                    )
                )

            # Calculate weighted overall score
            total_weight = sum(c.weight for c in definition.criteria)
            if total_weight > 0:
                overall_score = sum(
                    cr.score * cr.criterion.weight for cr in criteria_results
                ) / total_weight
            else:
                overall_score = 0.0

            # Collect all blockers
            all_blockers: list[str] = []
            for cr in criteria_results:
                all_blockers.extend(cr.blockers)

            # Check overall threshold
            if overall_score < definition.min_overall_score:
                all_blockers.append(
                    f"Overall score {overall_score:.1f} < minimum {definition.min_overall_score:.1f}"
                )

            ready = len(all_blockers) == 0

            readiness = ReadinessScore(
                stage=stage,
                overall_score=overall_score,
                criteria_results=criteria_results,
                ready=ready,
                blockers=all_blockers,
                evaluated_at=datetime.now(UTC),
            )

            span.set_attribute("gate.overall_score", overall_score)
            span.set_attribute("gate.ready", ready)
            span.set_attribute("gate.blocker_count", len(all_blockers))

            logger.info(
                "gate_readiness_evaluated",
                stage=str(stage),
                branch=branch,
                overall_score=round(overall_score, 2),
                ready=ready,
                blocker_count=len(all_blockers),
            )

            return readiness

    async def request_transition(
        self, stage: GateStage, branch: str, requestor: str
    ) -> GateTransition:
        """Create a gate transition request.

        Evaluates readiness and creates a pending transition. If readiness
        criteria are not met, the transition is created but marked with
        blockers in the readiness score.
        """
        with tracer.start_as_current_span("gate.request_transition") as span:
            span.set_attribute("gate.stage", str(stage))
            span.set_attribute("branch", branch)
            span.set_attribute("gate.requestor", requestor)

            readiness = await self.evaluate_readiness(stage, branch)
            current_stage = self._current_stages.get(branch)

            transition = GateTransition(
                id=uuid4(),
                from_stage=current_stage,
                to_stage=stage,
                readiness_score=readiness,
                comment=f"Transition requested by {requestor}",
                status=GateTransitionStatus.PENDING,
            )

            self._transitions[transition.id] = transition

            # Emit event
            await self._emit_gate_event(
                "gate.transition.requested",
                transition=transition,
                branch=branch,
            )

            logger.info(
                "gate_transition_requested",
                transition_id=str(transition.id),
                from_stage=str(current_stage) if current_stage else None,
                to_stage=str(stage),
                ready=readiness.ready,
                requestor=requestor,
            )

            return transition

    async def approve_transition(
        self, transition_id: UUID, approver: str, comment: str = ""
    ) -> GateTransition:
        """Approve a pending gate transition.

        Updates the transition status, records the current stage, and
        emits an approval event.
        """
        with tracer.start_as_current_span("gate.approve_transition") as span:
            span.set_attribute("gate.transition_id", str(transition_id))
            span.set_attribute("gate.approver", approver)

            transition = self._transitions.get(transition_id)
            if transition is None:
                raise ValueError(f"Transition {transition_id} not found")
            if transition.status != GateTransitionStatus.PENDING:
                raise ValueError(
                    f"Transition {transition_id} is {transition.status}, not pending"
                )

            now = datetime.now(UTC)
            transition.status = GateTransitionStatus.APPROVED
            transition.approved_by = approver
            transition.approved_at = now
            transition.comment = comment or transition.comment

            # Update current stage for the branch
            # Determine the branch from the transition history context
            branch = self._find_branch_for_transition(transition_id)
            if branch:
                self._current_stages[branch] = transition.to_stage

            await self._emit_gate_event(
                "gate.transition.approved",
                transition=transition,
                branch=branch or "unknown",
            )

            logger.info(
                "gate_transition_approved",
                transition_id=str(transition_id),
                to_stage=str(transition.to_stage),
                approver=approver,
            )

            return transition

    async def reject_transition(
        self, transition_id: UUID, approver: str, comment: str = ""
    ) -> GateTransition:
        """Reject a pending gate transition."""
        with tracer.start_as_current_span("gate.reject_transition") as span:
            span.set_attribute("gate.transition_id", str(transition_id))
            span.set_attribute("gate.approver", approver)

            transition = self._transitions.get(transition_id)
            if transition is None:
                raise ValueError(f"Transition {transition_id} not found")
            if transition.status != GateTransitionStatus.PENDING:
                raise ValueError(
                    f"Transition {transition_id} is {transition.status}, not pending"
                )

            transition.status = GateTransitionStatus.REJECTED
            transition.approved_by = approver
            transition.approved_at = datetime.now(UTC)
            transition.comment = comment or transition.comment

            await self._emit_gate_event(
                "gate.transition.rejected",
                transition=transition,
                branch=self._find_branch_for_transition(transition_id) or "unknown",
            )

            logger.info(
                "gate_transition_rejected",
                transition_id=str(transition_id),
                to_stage=str(transition.to_stage),
                approver=approver,
            )

            return transition

    async def get_current_stage(self, branch: str = "main") -> GateStage | None:
        """Get the current gate stage for a branch."""
        return self._current_stages.get(branch)

    async def get_transition_history(
        self, branch: str = "main"
    ) -> list[GateTransition]:
        """Get all transitions, optionally filtered by branch context.

        Returns transitions in insertion order. Since transitions store
        from_stage, callers can reconstruct the progression timeline.
        """
        return list(self._transitions.values())

    # ── Private helpers ────────────────────────────────────────────────

    async def _score_criterion(
        self,
        criterion: GateCriterion,
        branch: str,
        constraint_result: object,
    ) -> float:
        """Dispatch to the appropriate scoring function for a criterion type."""
        from twin_core.constraint_engine.models import ConstraintEvaluationResult

        if criterion.type == GateCriterionType.REQUIREMENT_COVERAGE:
            return await calculate_requirement_coverage(self._twin, branch)
        elif criterion.type == GateCriterionType.BOM_RISK:
            return await calculate_bom_risk(self._twin, branch)
        elif criterion.type == GateCriterionType.CONSTRAINT_COMPLIANCE:
            assert isinstance(constraint_result, ConstraintEvaluationResult)
            return await calculate_constraint_compliance(constraint_result)
        elif criterion.type == GateCriterionType.TEST_EVIDENCE:
            return await calculate_test_evidence(self._twin, branch)
        elif criterion.type == GateCriterionType.DESIGN_REVIEW:
            return await calculate_design_review(self._twin, branch)
        else:
            logger.warning("unknown_criterion_type", type=str(criterion.type))
            return 0.0

    async def _emit_gate_event(
        self, event_name: str, transition: GateTransition, branch: str
    ) -> None:
        """Emit a gate event on the event bus."""
        event = Event(
            id=str(uuid4()),
            type=EventType.APPROVAL_REQUESTED,  # Closest existing event type
            timestamp=datetime.now(UTC).isoformat(),
            source="gate_engine",
            data={
                "event_name": event_name,
                "transition_id": str(transition.id),
                "from_stage": str(transition.from_stage) if transition.from_stage else None,
                "to_stage": str(transition.to_stage),
                "status": str(transition.status),
                "overall_score": transition.readiness_score.overall_score,
                "ready": transition.readiness_score.ready,
                "branch": branch,
                "approved_by": transition.approved_by,
            },
        )
        await self._event_bus.publish(event)

    def _find_branch_for_transition(self, transition_id: UUID) -> str | None:
        """Find which branch a transition was requested for.

        Since we track transitions globally, we check the event data or
        fall back to searching current stages.
        """
        # Simple approach: check which branches have the relevant stage
        transition = self._transitions.get(transition_id)
        if transition is None:
            return None

        for branch, stage in self._current_stages.items():
            if stage == transition.from_stage:
                return branch

        # Default to main if no match
        return "main"
