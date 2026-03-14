"""Unit tests for gate approval workflow (MET-171).

Tests the GateApprovalService that integrates the gate engine with
a human-in-the-loop approval workflow for EVT/DVT/PVT transitions.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from digital_twin.thread.gate_engine.approval import GateApprovalService, _PendingApproval
from digital_twin.thread.gate_engine.engine import GateEngine
from digital_twin.thread.gate_engine.models import (
    GateApprovalResult,
    GateCriterion,
    GateCriterionType,
    GateDefinition,
    GateStage,
    GateTransitionRequest,
    GateTransitionStatus,
)
from orchestrator.event_bus.events import EventType
from orchestrator.event_bus.subscribers import EventBus
from tests.conftest import SpySubscriber
from twin_core.api import InMemoryTwinAPI
from twin_core.constraint_engine.validator import InMemoryConstraintEngine
from twin_core.graph_engine import InMemoryGraphEngine
from twin_core.models.enums import WorkProductType
from twin_core.models.work_product import WorkProduct

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_work_product(
    name: str = "test-art",
    art_type: WorkProductType = WorkProductType.CAD_MODEL,
    domain: str = "mechanical",
    metadata: dict | None = None,
) -> WorkProduct:
    return WorkProduct(
        name=name,
        type=art_type,
        domain=domain,
        file_path=f"work_products/{name}",
        content_hash="hash123",
        format="step",
        created_by="test",
        metadata=metadata or {},
    )


def _easy_gate_defs() -> dict[GateStage, GateDefinition]:
    """Gate definitions with low thresholds so empty twin passes."""
    return {
        GateStage.EVT: GateDefinition(
            stage=GateStage.EVT,
            min_overall_score=0.0,
            criteria=[
                GateCriterion(
                    type=GateCriterionType.DESIGN_REVIEW,
                    name="Design Review",
                    description="All reviewed",
                    weight=1.0,
                    threshold=0.0,
                    required=False,
                ),
            ],
        ),
        GateStage.DVT: GateDefinition(
            stage=GateStage.DVT,
            min_overall_score=0.0,
            criteria=[
                GateCriterion(
                    type=GateCriterionType.DESIGN_REVIEW,
                    name="Design Review",
                    description="All reviewed",
                    weight=1.0,
                    threshold=0.0,
                    required=False,
                ),
            ],
        ),
    }


def _hard_gate_defs() -> dict[GateStage, GateDefinition]:
    """Gate definitions with high thresholds so unreviewed work_products fail."""
    return {
        GateStage.EVT: GateDefinition(
            stage=GateStage.EVT,
            min_overall_score=90.0,
            criteria=[
                GateCriterion(
                    type=GateCriterionType.DESIGN_REVIEW,
                    name="Design Review",
                    description="All reviewed",
                    weight=1.0,
                    threshold=90.0,
                    required=True,
                ),
            ],
        ),
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def twin() -> InMemoryTwinAPI:
    return InMemoryTwinAPI.create()


@pytest.fixture
def graph() -> InMemoryGraphEngine:
    return InMemoryGraphEngine()


@pytest.fixture
def constraint_engine(graph: InMemoryGraphEngine) -> InMemoryConstraintEngine:
    return InMemoryConstraintEngine(graph)


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


@pytest.fixture
def spy(event_bus: EventBus) -> SpySubscriber:
    subscriber = SpySubscriber()
    event_bus.subscribe(subscriber)
    return subscriber


@pytest.fixture
def easy_engine(
    twin: InMemoryTwinAPI,
    constraint_engine: InMemoryConstraintEngine,
    event_bus: EventBus,
) -> GateEngine:
    return GateEngine(twin, constraint_engine, event_bus, _easy_gate_defs())


@pytest.fixture
def hard_engine(
    twin: InMemoryTwinAPI,
    constraint_engine: InMemoryConstraintEngine,
    event_bus: EventBus,
) -> GateEngine:
    return GateEngine(twin, constraint_engine, event_bus, _hard_gate_defs())


@pytest.fixture
def approval_service(
    easy_engine: GateEngine,
    event_bus: EventBus,
) -> GateApprovalService:
    return GateApprovalService(easy_engine, event_bus)


@pytest.fixture
def strict_service(
    hard_engine: GateEngine,
    event_bus: EventBus,
) -> GateApprovalService:
    return GateApprovalService(hard_engine, event_bus)


# ---------------------------------------------------------------------------
# Test: Gate transition blocked without approval
# ---------------------------------------------------------------------------


class TestTransitionBlockedWithoutApproval:
    """Gate transition should be blocked when readiness score is below threshold."""

    async def test_request_blocked_when_not_ready(
        self,
        strict_service: GateApprovalService,
        twin: InMemoryTwinAPI,
    ):
        """Transition request is immediately rejected when readiness fails."""
        # Add unreviewed work_product so design review score = 0%
        art = _make_work_product("unreviewed-part")
        await twin.create_work_product(art)

        result = await strict_service.request_transition(
            project_id="proj-1",
            from_gate=None,
            to_gate=GateStage.EVT,
            requestor_id="engineer1",
        )

        # Should be an immediate GateApprovalResult (not pending)
        assert isinstance(result, GateApprovalResult)
        assert result.status == GateTransitionStatus.REJECTED
        assert result.approver_id == "system"
        assert result.readiness_score is not None
        assert result.readiness_score.ready is False

    async def test_request_creates_pending_when_ready(
        self,
        approval_service: GateApprovalService,
    ):
        """Transition request creates a pending approval when readiness passes."""
        result = await approval_service.request_transition(
            project_id="proj-1",
            from_gate=None,
            to_gate=GateStage.EVT,
            requestor_id="engineer1",
        )

        assert isinstance(result, _PendingApproval)
        assert result.request.to_gate == GateStage.EVT
        assert result.readiness.ready is True


# ---------------------------------------------------------------------------
# Test: Readiness report included in approval context
# ---------------------------------------------------------------------------


class TestReadinessReportInContext:
    """Readiness report must be included in approval context."""

    async def test_pending_approval_has_readiness(
        self,
        approval_service: GateApprovalService,
    ):
        """Pending approval includes the readiness score."""
        result = await approval_service.request_transition(
            project_id="proj-1",
            from_gate=None,
            to_gate=GateStage.EVT,
            requestor_id="engineer1",
        )

        assert isinstance(result, _PendingApproval)
        assert result.readiness is not None
        assert result.readiness.stage == GateStage.EVT
        assert result.readiness.overall_score >= 0.0

    async def test_approved_result_has_readiness(
        self,
        approval_service: GateApprovalService,
    ):
        """Approval result includes the readiness score."""
        pending = await approval_service.request_transition(
            project_id="proj-1",
            from_gate=None,
            to_gate=GateStage.EVT,
            requestor_id="engineer1",
        )
        assert isinstance(pending, _PendingApproval)

        result = await approval_service.approve(pending.request_id, "approver1", "Approved")

        assert result.readiness_score is not None
        assert result.readiness_score.stage == GateStage.EVT


# ---------------------------------------------------------------------------
# Test: Override with justification in audit trail
# ---------------------------------------------------------------------------


class TestOverrideWithJustification:
    """Override must record justification in the audit trail."""

    async def test_override_records_justification(
        self,
        approval_service: GateApprovalService,
    ):
        """Override creates an OVERRIDDEN result with justification."""
        pending = await approval_service.request_transition(
            project_id="proj-1",
            from_gate=None,
            to_gate=GateStage.EVT,
            requestor_id="engineer1",
        )
        assert isinstance(pending, _PendingApproval)

        result = await approval_service.override(
            pending.request_id,
            "lead-engineer",
            "Critical timeline pressure, blockers accepted",
        )

        assert result.status == GateTransitionStatus.OVERRIDDEN
        assert result.override_justification == "Critical timeline pressure, blockers accepted"
        assert result.approver_id == "lead-engineer"

    async def test_override_in_audit_trail(
        self,
        approval_service: GateApprovalService,
    ):
        """Override is recorded in the audit trail."""
        pending = await approval_service.request_transition(
            project_id="proj-1",
            from_gate=None,
            to_gate=GateStage.EVT,
            requestor_id="engineer1",
        )
        assert isinstance(pending, _PendingApproval)

        await approval_service.override(
            pending.request_id,
            "lead-engineer",
            "Schedule pressure",
        )

        trail = approval_service.get_audit_trail()
        assert len(trail) == 1
        assert trail[0].status == GateTransitionStatus.OVERRIDDEN
        assert trail[0].override_justification == "Schedule pressure"

    async def test_override_requires_justification(
        self,
        approval_service: GateApprovalService,
    ):
        """Override with empty justification raises ValueError."""
        pending = await approval_service.request_transition(
            project_id="proj-1",
            from_gate=None,
            to_gate=GateStage.EVT,
            requestor_id="engineer1",
        )
        assert isinstance(pending, _PendingApproval)

        with pytest.raises(ValueError, match="justification"):
            await approval_service.override(pending.request_id, "lead-engineer", "")


# ---------------------------------------------------------------------------
# Test: Events emitted
# ---------------------------------------------------------------------------


class TestGateEventEmission:
    """GATE_REQUESTED, GATE_APPROVED, GATE_REJECTED events must be emitted."""

    async def test_gate_requested_event(
        self,
        approval_service: GateApprovalService,
        spy: SpySubscriber,
    ):
        """GATE_REQUESTED event is emitted when readiness passes."""
        await approval_service.request_transition(
            project_id="proj-1",
            from_gate=None,
            to_gate=GateStage.EVT,
            requestor_id="engineer1",
        )

        gate_events = [e for e in spy.received if e.type == EventType.GATE_REQUESTED]
        assert len(gate_events) == 1
        assert gate_events[0].source == "gate_approval_service"
        assert gate_events[0].data["to_gate"] == "EVT"
        assert gate_events[0].data["project_id"] == "proj-1"

    async def test_gate_approved_event(
        self,
        approval_service: GateApprovalService,
        spy: SpySubscriber,
    ):
        """GATE_APPROVED event is emitted on approval."""
        pending = await approval_service.request_transition(
            project_id="proj-1",
            from_gate=None,
            to_gate=GateStage.EVT,
            requestor_id="engineer1",
        )
        assert isinstance(pending, _PendingApproval)

        await approval_service.approve(pending.request_id, "approver1", "Ship it")

        gate_approved = [e for e in spy.received if e.type == EventType.GATE_APPROVED]
        assert len(gate_approved) == 1
        assert gate_approved[0].data["approver_id"] == "approver1"
        assert gate_approved[0].data["comment"] == "Ship it"

    async def test_gate_rejected_event(
        self,
        approval_service: GateApprovalService,
        spy: SpySubscriber,
    ):
        """GATE_REJECTED event is emitted on rejection."""
        pending = await approval_service.request_transition(
            project_id="proj-1",
            from_gate=None,
            to_gate=GateStage.EVT,
            requestor_id="engineer1",
        )
        assert isinstance(pending, _PendingApproval)

        await approval_service.reject(pending.request_id, "reviewer1", "Needs more testing")

        gate_rejected = [e for e in spy.received if e.type == EventType.GATE_REJECTED]
        assert len(gate_rejected) == 1
        assert gate_rejected[0].data["approver_id"] == "reviewer1"
        assert gate_rejected[0].data["comment"] == "Needs more testing"


# ---------------------------------------------------------------------------
# Test: Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    async def test_approve_nonexistent_raises(
        self,
        approval_service: GateApprovalService,
    ):
        """Approving a non-existent request raises ValueError."""
        with pytest.raises(ValueError, match="not found"):
            await approval_service.approve(uuid4(), "approver1")

    async def test_reject_nonexistent_raises(
        self,
        approval_service: GateApprovalService,
    ):
        """Rejecting a non-existent request raises ValueError."""
        with pytest.raises(ValueError, match="not found"):
            await approval_service.reject(uuid4(), "approver1")


# ---------------------------------------------------------------------------
# Test: Pydantic models
# ---------------------------------------------------------------------------


class TestPydanticModels:
    def test_gate_transition_request_model(self):
        """GateTransitionRequest fields are correct."""
        req = GateTransitionRequest(
            project_id="proj-1",
            from_gate=GateStage.EVT,
            to_gate=GateStage.DVT,
            requestor_id="engineer1",
            branch="main",
        )
        assert req.project_id == "proj-1"
        assert req.from_gate == GateStage.EVT
        assert req.to_gate == GateStage.DVT
        assert req.requestor_id == "engineer1"
        assert req.branch == "main"

    def test_gate_transition_request_defaults(self):
        """GateTransitionRequest defaults are correct."""
        req = GateTransitionRequest(
            project_id="proj-1",
            to_gate=GateStage.EVT,
            requestor_id="engineer1",
        )
        assert req.from_gate is None
        assert req.branch == "main"
