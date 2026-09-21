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
    evaluate_g3_feasibility,
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


@pytest.fixture
def twin():
    return InMemoryTwinAPI.create()


@pytest.fixture
def project_id():
    return uuid4()


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
