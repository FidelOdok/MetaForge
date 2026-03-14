"""Unit tests for the EVT/DVT/PVT gate engine."""

from __future__ import annotations

from uuid import uuid4

import pytest

from digital_twin.thread.gate_engine.engine import DEFAULT_GATE_DEFINITIONS, GateEngine
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
    calculate_requirement_coverage,
    calculate_test_evidence,
)
from orchestrator.event_bus.subscribers import EventBus
from tests.conftest import SpySubscriber
from twin_core.api import InMemoryTwinAPI
from twin_core.constraint_engine.models import (
    ConstraintEvaluationResult,
    ConstraintViolation,
)
from twin_core.constraint_engine.validator import InMemoryConstraintEngine
from twin_core.graph_engine import InMemoryGraphEngine
from twin_core.models.component import Component
from twin_core.models.enums import ConstraintSeverity, EdgeType, WorkProductType
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


def _make_component(
    part_number: str = "STM32F4",
    manufacturer: str = "ST",
    risk_score: float | None = None,
) -> Component:
    specs = {}
    if risk_score is not None:
        specs["risk_score"] = risk_score
    return Component(
        part_number=part_number,
        manufacturer=manufacturer,
        description="Test component",
        specs=specs,
    )


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
def gate_engine(
    twin: InMemoryTwinAPI,
    constraint_engine: InMemoryConstraintEngine,
    event_bus: EventBus,
) -> GateEngine:
    return GateEngine(twin, constraint_engine, event_bus)


# ---------------------------------------------------------------------------
# Test GateStage enum
# ---------------------------------------------------------------------------


class TestGateStage:
    def test_evt_value(self):
        assert GateStage.EVT == "EVT"

    def test_dvt_value(self):
        assert GateStage.DVT == "DVT"

    def test_pvt_value(self):
        assert GateStage.PVT == "PVT"

    def test_all_stages(self):
        assert set(GateStage) == {GateStage.EVT, GateStage.DVT, GateStage.PVT}


# ---------------------------------------------------------------------------
# Test models
# ---------------------------------------------------------------------------


class TestGateModels:
    def test_gate_criterion_valid(self):
        criterion = GateCriterion(
            type=GateCriterionType.REQUIREMENT_COVERAGE,
            name="Requirement Coverage",
            description="Test requirement coverage",
            weight=0.5,
            threshold=70.0,
            required=True,
        )
        assert criterion.weight == 0.5
        assert criterion.threshold == 70.0
        assert criterion.required is True

    def test_criterion_result(self):
        criterion = GateCriterion(
            type=GateCriterionType.BOM_RISK,
            name="BOM Risk",
            description="BOM risk score",
            weight=0.3,
            threshold=60.0,
        )
        result = CriterionResult(
            criterion=criterion,
            score=75.0,
            passed=True,
            details="Score: 75.0/60.0",
            blockers=[],
        )
        assert result.score == 75.0
        assert result.passed is True
        assert result.blockers == []

    def test_readiness_score(self):
        from datetime import UTC, datetime

        readiness = ReadinessScore(
            stage=GateStage.EVT,
            overall_score=82.5,
            criteria_results=[],
            ready=True,
            blockers=[],
            evaluated_at=datetime.now(UTC),
        )
        assert readiness.stage == GateStage.EVT
        assert readiness.overall_score == 82.5
        assert readiness.ready is True

    def test_gate_transition_defaults(self):
        from datetime import UTC, datetime

        readiness = ReadinessScore(
            stage=GateStage.DVT,
            overall_score=80.0,
            criteria_results=[],
            ready=True,
            blockers=[],
            evaluated_at=datetime.now(UTC),
        )
        transition = GateTransition(
            to_stage=GateStage.DVT,
            readiness_score=readiness,
        )
        assert transition.status == GateTransitionStatus.PENDING
        assert transition.from_stage is None
        assert transition.approved_by is None
        assert transition.id is not None

    def test_gate_definition(self):
        defn = GateDefinition(
            stage=GateStage.EVT,
            criteria=[],
            min_overall_score=60.0,
        )
        assert defn.stage == GateStage.EVT
        assert defn.min_overall_score == 60.0


# ---------------------------------------------------------------------------
# Test scoring functions
# ---------------------------------------------------------------------------


class TestRequirementCoverage:
    async def test_no_requirements_returns_100(self, twin: InMemoryTwinAPI):
        score = await calculate_requirement_coverage(twin, "main")
        assert score == 100.0

    async def test_requirements_with_evidence(self, twin: InMemoryTwinAPI):
        # Create 5 PRD work_products (requirements)
        reqs = []
        for i in range(5):
            art = _make_work_product(f"req-{i}", art_type=WorkProductType.PRD)
            await twin.create_work_product(art)
            reqs.append(art)

        # Create test evidence work_products and link 4 of 5
        for i in range(4):
            evidence = _make_work_product(f"evidence-{i}", art_type=WorkProductType.TEST_RESULT)
            await twin.create_work_product(evidence)
            await twin.add_edge(
                reqs[i].id,
                evidence.id,
                EdgeType.VALIDATES,
                metadata={"type": "test_evidence"},
            )

        score = await calculate_requirement_coverage(twin, "main")
        assert score == pytest.approx(80.0)


class TestBomRisk:
    async def test_no_components_returns_100(self, twin: InMemoryTwinAPI):
        score = await calculate_bom_risk(twin, "main")
        assert score == 100.0

    async def test_avg_risk_30_gives_score_70(self, twin: InMemoryTwinAPI):
        # Create components with risk scores averaging 30
        for i, risk in enumerate([20.0, 30.0, 40.0]):
            comp = _make_component(f"COMP-{i}", risk_score=risk)
            await twin.add_component(comp)

        score = await calculate_bom_risk(twin, "main")
        assert score == pytest.approx(70.0)


class TestConstraintCompliance:
    async def test_no_constraints_returns_100(self):
        result = ConstraintEvaluationResult(passed=True, evaluated_count=0)
        score = await calculate_constraint_compliance(result)
        assert score == 100.0

    async def test_8_of_10_passing(self):
        from datetime import UTC, datetime

        violations = [
            ConstraintViolation(
                constraint_id=uuid4(),
                constraint_name=f"fail-{i}",
                severity=ConstraintSeverity.ERROR,
                message="Failed",
                expression="False",
                evaluated_at=datetime.now(UTC),
            )
            for i in range(2)
        ]
        result = ConstraintEvaluationResult(
            passed=False,
            violations=violations,
            evaluated_count=10,
        )
        score = await calculate_constraint_compliance(result)
        assert score == pytest.approx(80.0)


class TestTestEvidence:
    async def test_no_test_plans_returns_100(self, twin: InMemoryTwinAPI):
        score = await calculate_test_evidence(twin, "main")
        assert score == 100.0

    async def test_partial_test_coverage(self, twin: InMemoryTwinAPI):
        # Create 4 test plans, 3 with results
        plans = []
        for i in range(4):
            tp = _make_work_product(f"test-plan-{i}", art_type=WorkProductType.TEST_PLAN)
            await twin.create_work_product(tp)
            plans.append(tp)

        for i in range(3):
            tr = _make_work_product(f"test-result-{i}", art_type=WorkProductType.TEST_RESULT)
            await twin.create_work_product(tr)
            await twin.add_edge(
                plans[i].id,
                tr.id,
                EdgeType.VALIDATES,
                metadata={"type": "test_result"},
            )

        score = await calculate_test_evidence(twin, "main")
        assert score == pytest.approx(75.0)


# ---------------------------------------------------------------------------
# Test evaluate_readiness
# ---------------------------------------------------------------------------


class TestEvaluateReadiness:
    async def test_weighted_overall_score(
        self,
        twin: InMemoryTwinAPI,
        constraint_engine: InMemoryConstraintEngine,
        event_bus: EventBus,
    ):
        """All criteria return 100 (empty twin) -> overall should be 100."""
        engine = GateEngine(twin, constraint_engine, event_bus)
        readiness = await engine.evaluate_readiness(GateStage.EVT, "main")

        # With an empty twin, all scoring functions return 100.0
        assert readiness.overall_score == pytest.approx(100.0)
        assert readiness.ready is True
        assert readiness.blockers == []

    async def test_custom_definitions(
        self,
        twin: InMemoryTwinAPI,
        constraint_engine: InMemoryConstraintEngine,
        event_bus: EventBus,
    ):
        """Custom gate definition with a single criterion."""
        custom_def = {
            GateStage.EVT: GateDefinition(
                stage=GateStage.EVT,
                min_overall_score=90.0,
                criteria=[
                    GateCriterion(
                        type=GateCriterionType.DESIGN_REVIEW,
                        name="Design Review",
                        description="All work_products reviewed",
                        weight=1.0,
                        threshold=90.0,
                        required=True,
                    ),
                ],
            ),
        }
        engine = GateEngine(twin, constraint_engine, event_bus, gate_definitions=custom_def)

        # Add an work_product without review approval -> score = 0%
        art = _make_work_product("unreviewed")
        await twin.create_work_product(art)

        readiness = await engine.evaluate_readiness(GateStage.EVT, "main")
        assert readiness.overall_score == pytest.approx(0.0)
        assert readiness.ready is False
        assert len(readiness.blockers) > 0


# ---------------------------------------------------------------------------
# Test gate blocks/allows transition
# ---------------------------------------------------------------------------


class TestGateTransitionBlocking:
    async def test_blocks_when_not_ready(
        self,
        twin: InMemoryTwinAPI,
        constraint_engine: InMemoryConstraintEngine,
        event_bus: EventBus,
    ):
        """Transition request is created but readiness is False when score < threshold."""
        custom_def = {
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
        engine = GateEngine(twin, constraint_engine, event_bus, gate_definitions=custom_def)

        # Add unreviewed work_product
        art = _make_work_product("part-a")
        await twin.create_work_product(art)

        transition = await engine.request_transition(GateStage.EVT, "main", "engineer1")
        assert transition.status == GateTransitionStatus.PENDING
        assert transition.readiness_score.ready is False
        assert len(transition.readiness_score.blockers) > 0

    async def test_allows_when_ready(
        self,
        twin: InMemoryTwinAPI,
        constraint_engine: InMemoryConstraintEngine,
        event_bus: EventBus,
    ):
        """Transition request has readiness True when all criteria met."""
        custom_def = {
            GateStage.EVT: GateDefinition(
                stage=GateStage.EVT,
                min_overall_score=50.0,
                criteria=[
                    GateCriterion(
                        type=GateCriterionType.DESIGN_REVIEW,
                        name="Design Review",
                        description="All reviewed",
                        weight=1.0,
                        threshold=50.0,
                        required=True,
                    ),
                ],
            ),
        }
        engine = GateEngine(twin, constraint_engine, event_bus, gate_definitions=custom_def)

        # Add reviewed work_product
        art = _make_work_product("reviewed-part", metadata={"review_status": "approved"})
        await twin.create_work_product(art)

        transition = await engine.request_transition(GateStage.EVT, "main", "engineer1")
        assert transition.readiness_score.ready is True
        assert transition.readiness_score.blockers == []


# ---------------------------------------------------------------------------
# Test approve/reject lifecycle
# ---------------------------------------------------------------------------


class TestTransitionLifecycle:
    async def test_approve_transition(self, gate_engine: GateEngine):
        transition = await gate_engine.request_transition(GateStage.EVT, "main", "requestor1")
        assert transition.status == GateTransitionStatus.PENDING

        approved = await gate_engine.approve_transition(transition.id, "approver1", "Looks good")
        assert approved.status == GateTransitionStatus.APPROVED
        assert approved.approved_by == "approver1"
        assert approved.approved_at is not None
        assert approved.comment == "Looks good"

    async def test_reject_transition(self, gate_engine: GateEngine):
        transition = await gate_engine.request_transition(GateStage.DVT, "main", "requestor1")

        rejected = await gate_engine.reject_transition(
            transition.id, "approver1", "Needs more testing"
        )
        assert rejected.status == GateTransitionStatus.REJECTED
        assert rejected.approved_by == "approver1"
        assert rejected.comment == "Needs more testing"

    async def test_approve_nonexistent_raises(self, gate_engine: GateEngine):
        with pytest.raises(ValueError, match="not found"):
            await gate_engine.approve_transition(uuid4(), "approver1")

    async def test_reject_nonexistent_raises(self, gate_engine: GateEngine):
        with pytest.raises(ValueError, match="not found"):
            await gate_engine.reject_transition(uuid4(), "approver1")

    async def test_approve_already_approved_raises(self, gate_engine: GateEngine):
        transition = await gate_engine.request_transition(GateStage.EVT, "main", "requestor1")
        await gate_engine.approve_transition(transition.id, "approver1")

        with pytest.raises(ValueError, match="not pending"):
            await gate_engine.approve_transition(transition.id, "approver2")

    async def test_reject_already_rejected_raises(self, gate_engine: GateEngine):
        transition = await gate_engine.request_transition(GateStage.EVT, "main", "requestor1")
        await gate_engine.reject_transition(transition.id, "approver1")

        with pytest.raises(ValueError, match="not pending"):
            await gate_engine.reject_transition(transition.id, "approver2")


# ---------------------------------------------------------------------------
# Test current stage tracking
# ---------------------------------------------------------------------------


class TestCurrentStage:
    async def test_initial_stage_is_none(self, gate_engine: GateEngine):
        stage = await gate_engine.get_current_stage("main")
        assert stage is None

    async def test_stage_updated_on_approval(self, gate_engine: GateEngine):
        transition = await gate_engine.request_transition(GateStage.EVT, "main", "requestor1")
        await gate_engine.approve_transition(transition.id, "approver1")

        stage = await gate_engine.get_current_stage("main")
        assert stage == GateStage.EVT


# ---------------------------------------------------------------------------
# Test transition history
# ---------------------------------------------------------------------------


class TestTransitionHistory:
    async def test_empty_history(self, gate_engine: GateEngine):
        history = await gate_engine.get_transition_history("main")
        assert history == []

    async def test_history_records_transitions(self, gate_engine: GateEngine):
        t1 = await gate_engine.request_transition(GateStage.EVT, "main", "user1")
        await gate_engine.approve_transition(t1.id, "approver1")

        _ = await gate_engine.request_transition(GateStage.DVT, "main", "user1")

        history = await gate_engine.get_transition_history("main")
        assert len(history) == 2
        assert history[0].to_stage == GateStage.EVT
        assert history[0].status == GateTransitionStatus.APPROVED
        assert history[1].to_stage == GateStage.DVT
        assert history[1].status == GateTransitionStatus.PENDING


# ---------------------------------------------------------------------------
# Test event emission
# ---------------------------------------------------------------------------


class TestEventEmission:
    async def test_emits_event_on_request(
        self,
        twin: InMemoryTwinAPI,
        constraint_engine: InMemoryConstraintEngine,
        event_bus: EventBus,
        spy: SpySubscriber,
    ):
        engine = GateEngine(twin, constraint_engine, event_bus)
        await engine.request_transition(GateStage.EVT, "main", "user1")

        assert len(spy.received) == 1
        event = spy.received[0]
        assert event.source == "gate_engine"
        assert event.data["event_name"] == "gate.transition.requested"
        assert event.data["to_stage"] == "EVT"

    async def test_emits_event_on_approval(
        self,
        twin: InMemoryTwinAPI,
        constraint_engine: InMemoryConstraintEngine,
        event_bus: EventBus,
        spy: SpySubscriber,
    ):
        engine = GateEngine(twin, constraint_engine, event_bus)
        transition = await engine.request_transition(GateStage.EVT, "main", "user1")
        await engine.approve_transition(transition.id, "approver1")

        assert len(spy.received) == 2
        approval_event = spy.received[1]
        assert approval_event.data["event_name"] == "gate.transition.approved"
        assert approval_event.data["status"] == "approved"

    async def test_emits_event_on_rejection(
        self,
        twin: InMemoryTwinAPI,
        constraint_engine: InMemoryConstraintEngine,
        event_bus: EventBus,
        spy: SpySubscriber,
    ):
        engine = GateEngine(twin, constraint_engine, event_bus)
        transition = await engine.request_transition(GateStage.DVT, "main", "user1")
        await engine.reject_transition(transition.id, "approver1", "Not ready")

        assert len(spy.received) == 2
        reject_event = spy.received[1]
        assert reject_event.data["event_name"] == "gate.transition.rejected"
        assert reject_event.data["status"] == "rejected"


# ---------------------------------------------------------------------------
# Test default gate definitions
# ---------------------------------------------------------------------------


class TestDefaultDefinitions:
    def test_all_stages_have_definitions(self):
        assert GateStage.EVT in DEFAULT_GATE_DEFINITIONS
        assert GateStage.DVT in DEFAULT_GATE_DEFINITIONS
        assert GateStage.PVT in DEFAULT_GATE_DEFINITIONS

    def test_evt_has_lower_thresholds_than_pvt(self):
        evt = DEFAULT_GATE_DEFINITIONS[GateStage.EVT]
        pvt = DEFAULT_GATE_DEFINITIONS[GateStage.PVT]
        assert evt.min_overall_score < pvt.min_overall_score

    def test_each_definition_has_criteria(self):
        for stage, defn in DEFAULT_GATE_DEFINITIONS.items():
            assert len(defn.criteria) > 0, f"{stage} has no criteria"
            total_weight = sum(c.weight for c in defn.criteria)
            assert total_weight == pytest.approx(1.0), f"{stage} weights sum to {total_weight}"
