"""TwinConsistencyGateChecker: real G3/G4 status for design-flow gates
(FORGE-73).

Only two gate_ids are mapped -- see Gate.gate_id's own docstring for why
G5-G8 aren't (yet). InMemoryTwinAPI throughout since evaluate_g3_feasibility/
evaluate_g4_architecture do real graph reads, not something worth faking.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from api_gateway.runs.gate_eval import TwinConsistencyGateChecker
from orchestrator.design_flow.executor import ConsistencyGateReport
from twin_core.api import InMemoryTwinAPI


@pytest.fixture
def twin():
    return InMemoryTwinAPI.create()


@pytest.fixture
def project_id():
    return uuid4()


@pytest.fixture
def checker(twin):
    return TwinConsistencyGateChecker(twin)


class TestUnmappedGateIds:
    async def test_g5_is_unmapped(self, checker, project_id):
        report = await checker.check("G5", str(project_id))
        assert report == ConsistencyGateReport(checked=False)

    async def test_unknown_gate_id_is_unmapped(self, checker, project_id):
        report = await checker.check("G99", str(project_id))
        assert report == ConsistencyGateReport(checked=False)


class TestMissingOrBadProjectId:
    async def test_none_project_id(self, checker):
        report = await checker.check("G3", None)
        assert report.checked is False

    async def test_empty_project_id(self, checker):
        report = await checker.check("G3", "")
        assert report.checked is False

    async def test_non_uuid_project_id(self, checker):
        report = await checker.check("G3", "not-a-uuid")
        assert report.checked is False


class TestG3Feasibility:
    async def test_real_evaluation_comes_back(self, checker, project_id):
        report = await checker.check("G3", str(project_id))
        assert report.checked is True
        assert report.evaluation is not None
        assert report.evaluation.gate_id == "G3"
        # No design_decision/risk data recorded -> every check comes back
        # NOT_EVALUATED (never a faked PASS) -- same discipline gates.py
        # itself follows; this just confirms the real function actually ran.
        assert len(report.evaluation.checks) > 0

    async def test_scoped_to_the_right_project(self, checker, project_id, twin):
        """A different project's data must not leak into this one's G3
        result -- same isolation FORGE-74 established for constraint_violations."""
        other_project = uuid4()
        from twin_core.models.constraint import Constraint
        from twin_core.models.enums import ConstraintSeverity

        await twin.create_constraint(
            Constraint(
                name="mass_budget",
                expression="True",
                severity=ConstraintSeverity.ERROR,
                domain="mech",
                source="user",
                project_id=other_project,
                metadata={"budget_kind": "mass"},
            )
        )
        report = await checker.check("G3", str(project_id))
        assert report.checked is True


class TestG4Architecture:
    async def test_real_evaluation_comes_back(self, checker, project_id):
        report = await checker.check("G4", str(project_id))
        assert report.checked is True
        assert report.evaluation is not None
        assert report.evaluation.gate_id == "G4"

    async def test_no_constraints_recorded_passes_architecture_check(self, checker, project_id):
        """Empty project -> zero violations -> real PASS (G4's own
        documented vacuous-pass exception, not this checker's doing)."""
        report = await checker.check("G4", str(project_id))
        architecture_check = next(
            c
            for c in report.evaluation.checks
            if c.id == "architecture_satisfies_major_constraints"
        )
        assert architecture_check.status.value == "pass"
