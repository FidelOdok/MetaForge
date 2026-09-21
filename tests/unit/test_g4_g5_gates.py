"""Unit tests for the G4 Architecture Gate and G5 Concept Selection Gate
evaluators (FORGE-61)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from twin_core.api import InMemoryTwinAPI
from twin_core.consistency import (
    GateCheckStatus,
    GateStatus,
    evaluate_g4_architecture,
    evaluate_g5_concept_selection,
)
from twin_core.models.constraint import Constraint
from twin_core.models.enums import ConstraintSeverity, WorkProductType
from twin_core.models.work_product import WorkProduct


@pytest.fixture
def twin():
    return InMemoryTwinAPI.create()


@pytest.fixture
def project_id():
    return uuid4()


async def _bound_constraint(
    twin, project_id, name="c1", expression="True", severity=ConstraintSeverity.ERROR
) -> Constraint:
    """A Constraint the engine can actually evaluate: `evaluate_constraints`
    (== `ConstraintEngine.evaluate_all`) only resolves constraints reachable
    via a CONSTRAINED_BY edge from a WorkProduct -- a bare
    `twin.create_constraint()` node (no binding) is invisible to it, same as
    `constraint_recorder.py`'s real usage always binds to the constraint_set
    work product."""
    wp = await twin.create_work_product(
        WorkProduct(
            name=f"{name}_binding",
            type=WorkProductType.CONSTRAINT_SET,
            domain="mech",
            file_path="",
            content_hash="h",
            format="md",
            created_by="user",
            project_id=project_id,
        )
    )
    node = Constraint(
        name=name,
        expression=expression,
        severity=severity,
        domain="mech",
        source="test",
        project_id=project_id,
    )
    return await twin.constraints.add_constraint(node, [wp.id])


def _decision(project_id, metadata: dict | None = None, name: str = "leg actuator") -> WorkProduct:
    return WorkProduct(
        name=name,
        type=WorkProductType.DESIGN_DECISION,
        domain="systems",
        file_path="",
        content_hash="h",
        format="md",
        created_by="user",
        project_id=project_id,
        metadata=metadata or {},
    )


class TestG4ConstraintCheck:
    async def test_no_violations_passes(self, twin, project_id):
        await _bound_constraint(twin, project_id, expression="True")
        result = await evaluate_g4_architecture(twin, project_id)
        check = next(c for c in result.checks if c.id == "architecture_satisfies_major_constraints")
        assert check.status == GateCheckStatus.PASS

    async def test_error_violation_fails(self, twin, project_id):
        await _bound_constraint(twin, project_id, expression="False")
        result = await evaluate_g4_architecture(twin, project_id)
        check = next(c for c in result.checks if c.id == "architecture_satisfies_major_constraints")
        assert check.status == GateCheckStatus.FAIL
        assert result.status == GateStatus.FAILED

    async def test_warning_violation_does_not_fail(self, twin, project_id):
        await _bound_constraint(
            twin, project_id, expression="False", severity=ConstraintSeverity.WARNING
        )
        result = await evaluate_g4_architecture(twin, project_id)
        check = next(c for c in result.checks if c.id == "architecture_satisfies_major_constraints")
        assert check.status == GateCheckStatus.PASS

    async def test_other_projects_violation_does_not_leak_in(self, twin, project_id):
        other = uuid4()
        await _bound_constraint(twin, other, expression="False")
        result = await evaluate_g4_architecture(twin, project_id)
        check = next(c for c in result.checks if c.id == "architecture_satisfies_major_constraints")
        assert check.status == GateCheckStatus.PASS


class TestG4NotEvaluatedChecks:
    async def test_subsystem_interface_and_friends_are_always_not_evaluated(self, twin, project_id):
        result = await evaluate_g4_architecture(twin, project_id)
        ids = {c.id for c in result.checks}
        for expected in (
            "critical_requirements_allocated",
            "subsystem_boundaries_defined",
            "interfaces_identified",
            "no_unowned_safety_critical_requirement",
        ):
            assert expected in ids
            check = next(c for c in result.checks if c.id == expected)
            assert check.status == GateCheckStatus.NOT_EVALUATED

    async def test_gate_id_is_g4(self, twin, project_id):
        result = await evaluate_g4_architecture(twin, project_id)
        assert result.gate_id == "G4"

    async def test_no_constraints_and_no_violations_is_ready_for_review(self, twin, project_id):
        result = await evaluate_g4_architecture(twin, project_id)
        assert result.status == GateStatus.READY_FOR_REVIEW


class TestG5NoDecisions:
    async def test_no_decisions_recorded_is_not_evaluated(self, twin, project_id):
        result = await evaluate_g5_concept_selection(twin, project_id)
        check = next(c for c in result.checks if c.id == "decisions:none-recorded")
        assert check.status == GateCheckStatus.NOT_EVALUATED
        assert result.status == GateStatus.READY_FOR_REVIEW

    async def test_gate_id_is_g5(self, twin, project_id):
        result = await evaluate_g5_concept_selection(twin, project_id)
        assert result.gate_id == "G5"


class TestG5TradeStudyCheck:
    async def test_alternatives_present_passes(self, twin, project_id):
        d = await twin.create_work_product(
            _decision(project_id, metadata={"rationale": "r", "alternatives": [{"option": "x"}]})
        )
        result = await evaluate_g5_concept_selection(twin, project_id)
        check = next(c for c in result.checks if c.id == f"decision:{d.id}:trade_study")
        assert check.status == GateCheckStatus.PASS

    async def test_no_alternatives_is_not_evaluated_not_a_fail(self, twin, project_id):
        d = await twin.create_work_product(_decision(project_id, metadata={"rationale": "r"}))
        result = await evaluate_g5_concept_selection(twin, project_id)
        check = next(c for c in result.checks if c.id == f"decision:{d.id}:trade_study")
        assert check.status == GateCheckStatus.NOT_EVALUATED


class TestG5RationaleCheck:
    async def test_rationale_present_passes(self, twin, project_id):
        d = await twin.create_work_product(_decision(project_id, metadata={"rationale": "why"}))
        result = await evaluate_g5_concept_selection(twin, project_id)
        check = next(c for c in result.checks if c.id == f"decision:{d.id}:rationale")
        assert check.status == GateCheckStatus.PASS

    async def test_missing_rationale_fails(self, twin, project_id):
        d = await twin.create_work_product(_decision(project_id, metadata={}))
        result = await evaluate_g5_concept_selection(twin, project_id)
        check = next(c for c in result.checks if c.id == f"decision:{d.id}:rationale")
        assert check.status == GateCheckStatus.FAIL
        assert result.status == GateStatus.FAILED


class TestG5LinkedCheck:
    async def test_parent_refs_present_passes(self, twin, project_id):
        d = await twin.create_work_product(
            _decision(project_id, metadata={"rationale": "r", "parent_refs": [str(uuid4())]})
        )
        result = await evaluate_g5_concept_selection(twin, project_id)
        check = next(c for c in result.checks if c.id == f"decision:{d.id}:linked")
        assert check.status == GateCheckStatus.PASS

    async def test_no_parent_refs_is_not_evaluated(self, twin, project_id):
        d = await twin.create_work_product(_decision(project_id, metadata={"rationale": "r"}))
        result = await evaluate_g5_concept_selection(twin, project_id)
        check = next(c for c in result.checks if c.id == f"decision:{d.id}:linked")
        assert check.status == GateCheckStatus.NOT_EVALUATED


class TestG5MultipleDecisions:
    async def test_each_decision_gets_its_own_checks(self, twin, project_id):
        d1 = await twin.create_work_product(
            _decision(project_id, name="d1", metadata={"rationale": "r1"})
        )
        d2 = await twin.create_work_product(
            _decision(project_id, name="d2", metadata={"rationale": "r2"})
        )
        result = await evaluate_g5_concept_selection(twin, project_id)
        ids = {c.id for c in result.checks}
        assert f"decision:{d1.id}:rationale" in ids
        assert f"decision:{d2.id}:rationale" in ids
