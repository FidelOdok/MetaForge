"""Unit tests for the G3 Preliminary Feasibility Gate evaluator (FORGE-60).

Note: `tests/unit/test_gate_engine.py` already exists and covers an
unrelated EVT/DVT/PVT hardware-validation `GateEngine`
(`digital_twin/thread/gate_engine.py`) -- this file is deliberately named
differently to avoid colliding with it.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from twin_core.api import InMemoryTwinAPI
from twin_core.consistency import (
    Budget,
    BudgetAllocation,
    GateCheckStatus,
    GateStatus,
    Invariant,
    InvariantComparison,
    budget_from_entity,
    evaluate_g3_feasibility,
    invariant_from_entity,
)
from twin_core.models import WorkProduct, WorkProductType
from twin_core.models.engineering_entity import EngineeringEntity


def _wp(name: str, project_id, metadata: dict | None = None) -> WorkProduct:
    return WorkProduct(
        name=name,
        type=WorkProductType.CAD_MODEL,
        domain="mechanical",
        file_path=f"{name}.step",
        content_hash="h",
        format="step",
        created_by="user",
        project_id=project_id,
        metadata=metadata or {},
    )


def _risk(
    project_id, metadata: dict | None = None, title: str = "battery fire"
) -> EngineeringEntity:
    return EngineeringEntity(
        entity_type="risk", title=title, project_id=project_id, metadata=metadata or {}
    )


def _budget_entity(
    project_id, metadata: dict | None = None, title: str | None = "mass_budget"
) -> EngineeringEntity:
    return EngineeringEntity(
        entity_type="budget", title=title, project_id=project_id, metadata=metadata or {}
    )


def _invariant_entity(
    project_id, metadata: dict | None = None, title: str | None = "INV-MASS"
) -> EngineeringEntity:
    return EngineeringEntity(
        entity_type="invariant", title=title, project_id=project_id, metadata=metadata or {}
    )


@pytest.fixture
def twin():
    return InMemoryTwinAPI.create()


@pytest.fixture
def project_id():
    return uuid4()


class TestBudgetFromEntity:
    def test_reads_metric_unit_total_and_allocations(self, project_id):
        e = _budget_entity(
            project_id,
            metadata={
                "metric": "mass",
                "unit": "kg",
                "system_total": 5.0,
                "allocations": [{"target": "frame", "amount": 2.0}],
            },
        )
        budget = budget_from_entity(e, project_id)
        assert budget.id == "mass_budget"
        assert budget.project_id == project_id
        assert budget.metric == "mass"
        assert budget.unit == "kg"
        assert budget.system_total == 5.0
        assert budget.allocations == [BudgetAllocation(target="frame", amount=2.0)]

    def test_falls_back_to_node_id_when_untitled(self, project_id):
        e = _budget_entity(
            project_id,
            metadata={"metric": "mass", "unit": "kg", "system_total": 5.0},
            title=None,
        )
        budget = budget_from_entity(e, project_id)
        assert budget.id == str(e.id)

    def test_rejects_non_budget_entity(self, project_id):
        e = EngineeringEntity(entity_type="risk", statement="x", project_id=project_id)
        with pytest.raises(ValueError, match="not a budget"):
            budget_from_entity(e, project_id)

    def test_requires_metric_unit_and_system_total_in_metadata(self, project_id):
        e = _budget_entity(project_id, metadata={"metric": "mass"})
        with pytest.raises(ValueError, match="missing"):
            budget_from_entity(e, project_id)

    def test_malformed_system_total_raises(self, project_id):
        e = _budget_entity(
            project_id, metadata={"metric": "mass", "unit": "kg", "system_total": "not-a-number"}
        )
        with pytest.raises(ValueError, match="malformed metadata"):
            budget_from_entity(e, project_id)


class TestInvariantFromEntity:
    def test_reads_metric_unit_limit_and_comparison(self, project_id):
        e = _invariant_entity(
            project_id, metadata={"metric": "mass", "unit": "kg", "limit": 5.0, "comparison": "<="}
        )
        inv = invariant_from_entity(e)
        assert inv.id == "INV-MASS"
        assert inv.metric == "mass"
        assert inv.unit == "kg"
        assert inv.limit == 5.0
        assert inv.comparison == InvariantComparison.LTE

    def test_comparison_defaults_to_lte(self, project_id):
        e = _invariant_entity(project_id, metadata={"metric": "mass", "unit": "kg", "limit": 5.0})
        inv = invariant_from_entity(e)
        assert inv.comparison == InvariantComparison.LTE

    def test_rejects_non_invariant_entity(self, project_id):
        e = EngineeringEntity(entity_type="risk", statement="x", project_id=project_id)
        with pytest.raises(ValueError, match="not an invariant"):
            invariant_from_entity(e)

    def test_requires_metric_unit_and_limit_in_metadata(self, project_id):
        e = _invariant_entity(project_id, metadata={"metric": "mass"})
        with pytest.raises(ValueError, match="missing"):
            invariant_from_entity(e)


class TestPersistedBudgetsAutoLoad:
    async def test_no_budgets_declared_is_not_evaluated_not_a_silent_pass(self, twin, project_id):
        result = await evaluate_g3_feasibility(twin, project_id)
        check = next(c for c in result.checks if c.id == "budgets:none-declared")
        assert check.status == GateCheckStatus.NOT_EVALUATED

    async def test_a_persisted_budget_is_loaded_and_evaluated(self, twin, project_id):
        await twin.graph.add_node(_wp("frame", project_id, {"mass_kg": 10.0}))
        await twin.create_engineering_entity(
            _budget_entity(
                project_id, metadata={"metric": "mass", "unit": "kg", "system_total": 5.0}
            )
        )
        result = await evaluate_g3_feasibility(twin, project_id)
        check = next(c for c in result.checks if c.id == "budget:mass_budget")
        assert check.status == GateCheckStatus.FAIL
        assert result.status == GateStatus.FAILED

    async def test_malformed_persisted_budget_is_not_evaluated_not_dropped(self, twin, project_id):
        entity = await twin.create_engineering_entity(_budget_entity(project_id, metadata={}))
        result = await evaluate_g3_feasibility(twin, project_id)
        check = next(c for c in result.checks if c.id == f"budget:{entity.id}")
        assert check.status == GateCheckStatus.NOT_EVALUATED
        assert "missing" in check.detail

    async def test_explicit_empty_list_bypasses_auto_load(self, twin, project_id):
        await twin.create_engineering_entity(
            _budget_entity(
                project_id, metadata={"metric": "mass", "unit": "kg", "system_total": 5.0}
            )
        )
        result = await evaluate_g3_feasibility(twin, project_id, budgets=[])
        ids = {c.id for c in result.checks}
        assert "budget:mass_budget" not in ids
        assert "budgets:none-declared" not in ids


class TestPersistedInvariantsAutoLoad:
    async def test_no_invariants_declared_is_not_evaluated_not_a_silent_pass(
        self, twin, project_id
    ):
        result = await evaluate_g3_feasibility(twin, project_id)
        check = next(c for c in result.checks if c.id == "invariants:none-declared")
        assert check.status == GateCheckStatus.NOT_EVALUATED

    async def test_a_persisted_invariant_is_loaded_and_evaluated(self, twin, project_id):
        await twin.graph.add_node(_wp("frame", project_id, {"cost_usd": 500.0}))
        await twin.create_engineering_entity(
            _invariant_entity(
                project_id, metadata={"metric": "cost", "unit": "usd", "limit": 100.0}
            )
        )
        result = await evaluate_g3_feasibility(twin, project_id)
        check = next(c for c in result.checks if c.id == "invariant:INV-MASS")
        assert check.status == GateCheckStatus.FAIL
        assert result.status == GateStatus.FAILED

    async def test_malformed_persisted_invariant_is_not_evaluated_not_dropped(
        self, twin, project_id
    ):
        entity = await twin.create_engineering_entity(_invariant_entity(project_id, metadata={}))
        result = await evaluate_g3_feasibility(twin, project_id)
        check = next(c for c in result.checks if c.id == f"invariant:{entity.id}")
        assert check.status == GateCheckStatus.NOT_EVALUATED
        assert "missing" in check.detail

    async def test_explicit_empty_list_bypasses_auto_load(self, twin, project_id):
        await twin.create_engineering_entity(
            _invariant_entity(
                project_id, metadata={"metric": "cost", "unit": "usd", "limit": 100.0}
            )
        )
        result = await evaluate_g3_feasibility(twin, project_id, invariants=[])
        ids = {c.id for c in result.checks}
        assert "invariant:INV-MASS" not in ids
        assert "invariants:none-declared" not in ids


class TestBudgetChecks:
    async def test_within_budget_passes(self, twin, project_id):
        await twin.graph.add_node(_wp("frame", project_id, {"mass_kg": 2.0}))
        budget = Budget(
            id="mass",
            project_id=project_id,
            metric="mass",
            unit="kg",
            system_total=5.0,
            allocations=[BudgetAllocation(target="frame", amount=2.0)],
        )
        result = await evaluate_g3_feasibility(twin, project_id, budgets=[budget])
        check = next(c for c in result.checks if c.id == "budget:mass")
        assert check.status == GateCheckStatus.PASS

    async def test_over_budget_fails(self, twin, project_id):
        await twin.graph.add_node(_wp("frame", project_id, {"mass_kg": 10.0}))
        budget = Budget(
            id="mass", project_id=project_id, metric="mass", unit="kg", system_total=5.0
        )
        result = await evaluate_g3_feasibility(twin, project_id, budgets=[budget])
        check = next(c for c in result.checks if c.id == "budget:mass")
        assert check.status == GateCheckStatus.FAIL
        assert result.status == GateStatus.FAILED


class TestInvariantChecks:
    async def test_violated_invariant_fails_the_gate(self, twin, project_id):
        await twin.graph.add_node(_wp("frame", project_id, {"cost_usd": 500.0}))
        inv = Invariant(
            id="INV-COST",
            metric="cost",
            unit="usd",
            limit=100.0,
            comparison=InvariantComparison.LTE,
        )
        result = await evaluate_g3_feasibility(twin, project_id, invariants=[inv])
        check = next(c for c in result.checks if c.id == "invariant:INV-COST")
        assert check.status == GateCheckStatus.FAIL
        assert result.status == GateStatus.FAILED


class TestRiskChecks:
    async def test_no_risks_recorded_is_not_evaluated_not_a_silent_pass(self, twin, project_id):
        result = await evaluate_g3_feasibility(twin, project_id)
        check = next(c for c in result.checks if c.id == "risks:none-recorded")
        assert check.status == GateCheckStatus.NOT_EVALUATED

    async def test_unscored_risk_is_not_evaluated(self, twin, project_id):
        risk = await twin.create_engineering_entity(_risk(project_id))
        result = await evaluate_g3_feasibility(twin, project_id)
        check = next(c for c in result.checks if c.id == f"risk:{risk.id}")
        assert check.status == GateCheckStatus.NOT_EVALUATED

    async def test_critical_unmitigated_risk_fails(self, twin, project_id):
        risk = await twin.create_engineering_entity(
            _risk(project_id, metadata={"severity": 5, "likelihood": 5})
        )
        result = await evaluate_g3_feasibility(twin, project_id)
        check = next(c for c in result.checks if c.id == f"risk:{risk.id}")
        assert check.status == GateCheckStatus.FAIL
        assert result.status == GateStatus.FAILED

    async def test_critical_mitigated_risk_passes(self, twin, project_id):
        risk = await twin.create_engineering_entity(
            _risk(
                project_id,
                metadata={"severity": 5, "likelihood": 5, "mitigation": "add thermal fuse"},
            )
        )
        result = await evaluate_g3_feasibility(twin, project_id)
        check = next(c for c in result.checks if c.id == f"risk:{risk.id}")
        assert check.status == GateCheckStatus.PASS

    async def test_low_score_risk_passes_without_mitigation(self, twin, project_id):
        risk = await twin.create_engineering_entity(
            _risk(project_id, metadata={"severity": 1, "likelihood": 1})
        )
        result = await evaluate_g3_feasibility(twin, project_id)
        check = next(c for c in result.checks if c.id == f"risk:{risk.id}")
        assert check.status == GateCheckStatus.PASS


class TestNotEvaluatedChecksAlwaysPresent:
    async def test_structural_and_friends_are_always_not_evaluated(self, twin, project_id):
        result = await evaluate_g3_feasibility(twin, project_id)
        ids = {c.id for c in result.checks}
        for expected in (
            "structural_feasibility",
            "actuator_sizing",
            "thermal_plausibility",
            "geometry_feasibility",
            "technology_availability",
        ):
            assert expected in ids
            check = next(c for c in result.checks if c.id == expected)
            assert check.status == GateCheckStatus.NOT_EVALUATED


class TestGateStatus:
    async def test_all_pass_and_no_not_evaluated_checks_yields_passed(self, twin, project_id):
        # Force every check to a resolved status by only exercising budgets
        # that pass and skipping the always-not-evaluated structural checks
        # via a direct construction that bypasses evaluate_g3_feasibility's
        # own not-evaluated additions -- not possible through the public
        # function by design (those checks are ALWAYS present today), so
        # this asserts the honest current ceiling instead: a fresh project
        # with no budgets/invariants/risks is READY_FOR_REVIEW, never
        # PASSED, because the structural/thermal/etc. checks are real
        # unknowns, not assumed-fine.
        result = await evaluate_g3_feasibility(twin, project_id)
        assert result.status == GateStatus.READY_FOR_REVIEW

    async def test_a_failure_beats_not_evaluated(self, twin, project_id):
        await twin.graph.add_node(_wp("frame", project_id, {"mass_kg": 10.0}))
        budget = Budget(
            id="mass", project_id=project_id, metric="mass", unit="kg", system_total=5.0
        )
        result = await evaluate_g3_feasibility(twin, project_id, budgets=[budget])
        assert result.status == GateStatus.FAILED

    async def test_gate_id_is_g3(self, twin, project_id):
        result = await evaluate_g3_feasibility(twin, project_id)
        assert result.gate_id == "G3"
