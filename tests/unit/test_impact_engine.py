"""Unit tests for ImpactEngine (FORGE-67): the real, pre-commit impact
graph + conflict detection + revalidation planner.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from twin_core.api import InMemoryTwinAPI
from twin_core.consistency.impact import ImpactEngine
from twin_core.consistency.staleness import Dependency, StalenessEngine
from twin_core.models.constraint import Constraint
from twin_core.models.engineering_entity import EngineeringEntity
from twin_core.models.enums import ConstraintSeverity, EdgeType
from twin_core.models.patch import Patch, PatchOp, PatchOperation


@pytest.fixture
def twin():
    return InMemoryTwinAPI.create()


@pytest.fixture
def project_id():
    return uuid4()


@pytest.fixture
def staleness(twin):
    return StalenessEngine(twin)


@pytest.fixture
def impact(twin):
    return ImpactEngine(twin)


async def _seed_requirement(twin, project_id, name="payload_mass") -> Constraint:
    return await twin.create_constraint(
        Constraint(
            name=name,
            expression="True",
            severity=ConstraintSeverity.ERROR,
            domain="mech",
            source="test",
            project_id=project_id,
        )
    )


async def _seed_evidence(twin, project_id, statement="sim result") -> EngineeringEntity:
    return await twin.create_engineering_entity(
        EngineeringEntity(entity_type="evidence", statement=statement, project_id=project_id)
    )


def _revise_patch(req: Constraint, *, project_id) -> Patch:
    return Patch(
        operations=[
            PatchOperation(
                op=PatchOp.REVISE,
                entity_kind="constraint",
                entity_id=req.id,
                fields={"message": "x"},
                expected_revision=req.revision,
            )
        ],
        reason="test",
        project_id=project_id,
    )


class TestAnalyseBasics:
    async def test_requires_project_id_on_the_patch(self, impact):
        req_id = uuid4()
        patch = Patch(
            operations=[
                PatchOperation(
                    op=PatchOp.REVISE,
                    entity_kind="constraint",
                    entity_id=req_id,
                    fields={"message": "x"},
                )
            ],
            reason="test",
        )
        with pytest.raises(ValueError, match="project_id"):
            await impact.analyse(patch, {})

    async def test_directly_changed_reflects_the_patch(self, impact, twin, project_id):
        req = await _seed_requirement(twin, project_id)
        report = await impact.analyse(_revise_patch(req, project_id=project_id), {})
        assert report.directly_changed == [req.id]

    async def test_add_operations_are_not_directly_changed(self, impact, twin, project_id):
        """An ADD creates a brand-new node -- nothing can depend on it yet,
        so it isn't part of the impact walk."""
        patch = Patch(
            operations=[
                PatchOperation(
                    op=PatchOp.ADD,
                    entity_kind="engineering_entity",
                    entity={"entity_type": "risk", "statement": "new risk"},
                )
            ],
            reason="test",
            project_id=project_id,
        )
        report = await impact.analyse(patch, {})
        assert report.directly_changed == []
        assert report.affected_objects == []


class TestTransitiveImpact:
    async def test_finds_dependent_evidence(self, impact, staleness, twin, project_id):
        req = await _seed_requirement(twin, project_id)
        evidence = await _seed_evidence(twin, project_id)
        await staleness.declare_dependencies(
            "engineering_entity",
            evidence.id,
            [Dependency(entity_kind="constraint", entity_id=req.id, revision=1)],
        )
        report = await impact.analyse(_revise_patch(req, project_id=project_id), {})
        assert evidence.id in report.affected_objects

    async def test_never_writes_anything(self, impact, staleness, twin, project_id):
        req = await _seed_requirement(twin, project_id)
        evidence = await _seed_evidence(twin, project_id)
        await staleness.declare_dependencies(
            "engineering_entity",
            evidence.id,
            [Dependency(entity_kind="constraint", entity_id=req.id, revision=1)],
        )
        await impact.analyse(_revise_patch(req, project_id=project_id), {})

        unchanged_req = await twin.get_constraint(req.id)
        assert unchanged_req.revision == 1
        status = await staleness.get_status("engineering_entity", evidence.id)
        assert status.value == "current"

    async def test_transitive_chain_reached(self, impact, staleness, twin, project_id):
        motor = await _seed_requirement(twin, project_id, name="motor_spec")
        mount = await _seed_evidence(twin, project_id, statement="mount rationale")
        simulation = await _seed_evidence(twin, project_id, statement="thermal sim")
        await staleness.declare_dependencies(
            "engineering_entity",
            mount.id,
            [Dependency(entity_kind="constraint", entity_id=motor.id, revision=1)],
        )
        await staleness.declare_dependencies(
            "engineering_entity",
            simulation.id,
            [Dependency(entity_kind="engineering_entity", entity_id=mount.id, revision=1)],
        )
        report = await impact.analyse(_revise_patch(motor, project_id=project_id), {})
        assert mount.id in report.affected_objects
        assert simulation.id in report.affected_objects

    async def test_no_dependents_yields_empty_affected_objects(self, impact, twin, project_id):
        req = await _seed_requirement(twin, project_id)
        report = await impact.analyse(_revise_patch(req, project_id=project_id), {})
        assert report.affected_objects == []


class TestConflicts:
    async def test_contradicting_evidence_reported_as_conflict(self, impact, twin, project_id):
        req = await _seed_requirement(twin, project_id)
        evidence = await _seed_evidence(twin, project_id, statement="torque too low")
        await twin.add_edge(evidence.id, req.id, EdgeType.CONFLICTS_WITH)

        report = await impact.analyse(_revise_patch(req, project_id=project_id), {})
        assert len(report.conflicts) == 1
        conflict = report.conflicts[0]
        assert conflict.requirement_id == req.id
        assert conflict.evidence_id == evidence.id
        assert conflict.severity == "high"  # ConstraintSeverity.ERROR -> high

    async def test_no_conflicting_evidence_yields_no_conflicts(self, impact, twin, project_id):
        req = await _seed_requirement(twin, project_id)
        report = await impact.analyse(_revise_patch(req, project_id=project_id), {})
        assert report.conflicts == []

    async def test_warning_severity_requirement_yields_medium_conflict(
        self, impact, twin, project_id
    ):
        req = await twin.create_constraint(
            Constraint(
                name="soft_target",
                expression="True",
                severity=ConstraintSeverity.WARNING,
                domain="mech",
                source="test",
                project_id=project_id,
            )
        )
        evidence = await _seed_evidence(twin, project_id)
        await twin.add_edge(evidence.id, req.id, EdgeType.CONFLICTS_WITH)
        report = await impact.analyse(_revise_patch(req, project_id=project_id), {})
        assert report.conflicts[0].severity == "medium"


class TestRevalidationPlan:
    async def test_evidence_dependent_gets_the_real_revalidation_action(
        self, impact, staleness, twin, project_id
    ):
        req = await _seed_requirement(twin, project_id)
        evidence = await _seed_evidence(twin, project_id)
        await staleness.declare_dependencies(
            "engineering_entity",
            evidence.id,
            [Dependency(entity_kind="constraint", entity_id=req.id, revision=1)],
        )
        report = await impact.analyse(_revise_patch(req, project_id=project_id), {})
        step = next(s for s in report.revalidation_plan if s.entity_id == evidence.id)
        assert "record_evidence" in step.action
        assert "supersedes" in step.action

    async def test_non_evidence_dependent_gets_a_manual_review_action(
        self, impact, staleness, twin, project_id
    ):
        req = await _seed_requirement(twin, project_id, name="upstream_req")
        downstream_need = await twin.create_engineering_entity(
            EngineeringEntity(entity_type="objective", statement="derived", project_id=project_id)
        )
        await staleness.declare_dependencies(
            "engineering_entity",
            downstream_need.id,
            [Dependency(entity_kind="constraint", entity_id=req.id, revision=1)],
        )
        report = await impact.analyse(_revise_patch(req, project_id=project_id), {})
        step = next(s for s in report.revalidation_plan if s.entity_id == downstream_need.id)
        assert "no automatic re-run" in step.action
