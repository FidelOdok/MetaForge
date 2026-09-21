"""Unit tests for StalenessEngine (FORGE-59)."""

from uuid import uuid4

import pytest

from twin_core.api import InMemoryTwinAPI
from twin_core.consistency.staleness import Dependency, StalenessEngine, StalenessStatus
from twin_core.models.constraint import Constraint
from twin_core.models.engineering_entity import EngineeringEntity
from twin_core.models.enums import ConstraintSeverity


@pytest.fixture
def twin():
    return InMemoryTwinAPI.create()


@pytest.fixture
def project_id():
    return uuid4()


@pytest.fixture
def engine(twin):
    return StalenessEngine(twin)


async def _seed_constraint(twin, project_id, name="req") -> Constraint:
    c = Constraint(
        name=name,
        expression="True",
        severity=ConstraintSeverity.ERROR,
        domain="mech",
        source="test",
        project_id=project_id,
    )
    return await twin.create_constraint(c)


async def _seed_evidence(twin, project_id, statement="sim result") -> EngineeringEntity:
    e = EngineeringEntity(entity_type="evidence", statement=statement, project_id=project_id)
    return await twin.create_engineering_entity(e)


class TestDeclareAndReadDependencies:
    async def test_default_status_is_current(self, engine, twin, project_id):
        ev = await _seed_evidence(twin, project_id)
        status = await engine.get_status("engineering_entity", ev.id)
        assert status == StalenessStatus.CURRENT

    async def test_set_status_persists(self, engine, twin, project_id):
        ev = await _seed_evidence(twin, project_id)
        await engine.set_status("engineering_entity", ev.id, StalenessStatus.INVALID)
        assert await engine.get_status("engineering_entity", ev.id) == StalenessStatus.INVALID

    async def test_declare_dependencies_does_not_disturb_status(self, engine, twin, project_id):
        req = await _seed_constraint(twin, project_id)
        ev = await _seed_evidence(twin, project_id)
        await engine.declare_dependencies(
            "engineering_entity",
            ev.id,
            [Dependency(entity_kind="constraint", entity_id=req.id, revision=1)],
        )
        assert await engine.get_status("engineering_entity", ev.id) == StalenessStatus.CURRENT

    async def test_unknown_entity_raises(self, engine):
        with pytest.raises(KeyError):
            await engine.get_status("constraint", uuid4())


class TestPropagate:
    async def test_doc_worked_example_requirement_supersedes_evidence(
        self, engine, twin, project_id
    ):
        """spec section 20: REQ-PAYLOAD-001@1 superseded -> EVID-SIM-008 -> STALE."""
        req = await _seed_constraint(twin, project_id, name="payload")
        evidence = await _seed_evidence(twin, project_id, statement="payload sim result")
        await engine.declare_dependencies(
            "engineering_entity",
            evidence.id,
            [Dependency(entity_kind="constraint", entity_id=req.id, revision=1)],
        )

        await twin.update_constraint(req.id, {"message": "revised payload limit"})  # -> revision 2

        markings = await engine.propagate(project_id, "constraint", req.id)

        assert len(markings) == 1
        assert markings[0].entity_id == evidence.id
        assert await engine.get_status("engineering_entity", evidence.id) == StalenessStatus.STALE

    async def test_low_impact_change_leaves_unrelated_evidence_current(
        self, engine, twin, project_id
    ):
        """spec section 21's low-impact example: a change to one thing must
        NOT mark evidence that never declared a dependency on it."""
        colour_req = await _seed_constraint(twin, project_id, name="housing_colour")
        unrelated_evidence = await _seed_evidence(twin, project_id, statement="structural fea")
        # unrelated_evidence declares NO dependency on colour_req at all.

        await twin.update_constraint(colour_req.id, {"message": "black -> silver"})
        markings = await engine.propagate(project_id, "constraint", colour_req.id)

        assert markings == []
        status = await engine.get_status("engineering_entity", unrelated_evidence.id)
        assert status == StalenessStatus.CURRENT

    async def test_no_marking_when_pinned_revision_still_current(self, engine, twin, project_id):
        req = await _seed_constraint(twin, project_id)
        evidence = await _seed_evidence(twin, project_id)
        await engine.declare_dependencies(
            "engineering_entity",
            evidence.id,
            [Dependency(entity_kind="constraint", entity_id=req.id, revision=1)],
        )
        # req is still at revision 1 -- nothing changed.
        markings = await engine.propagate(project_id, "constraint", req.id)
        assert markings == []

    async def test_transitive_propagation_reaches_downstream_of_downstream(
        self, engine, twin, project_id
    ):
        """spec section 21's motor-swap example: impact cascades through
        the dependency chain (mount CAD -> simulation -> BOM), not just
        the direct dependent."""
        motor = await _seed_constraint(twin, project_id, name="motor_spec")
        mount = await _seed_evidence(twin, project_id, statement="mount design rationale")
        simulation = await _seed_evidence(twin, project_id, statement="thermal simulation")

        await engine.declare_dependencies(
            "engineering_entity",
            mount.id,
            [Dependency(entity_kind="constraint", entity_id=motor.id, revision=1)],
        )
        await engine.declare_dependencies(
            "engineering_entity",
            simulation.id,
            [Dependency(entity_kind="engineering_entity", entity_id=mount.id, revision=1)],
        )

        await twin.update_constraint(motor.id, {"message": "MX-106 -> XM540"})
        markings = await engine.propagate(project_id, "constraint", motor.id)

        marked_ids = {m.entity_id for m in markings}
        assert mount.id in marked_ids
        assert simulation.id in marked_ids
        assert await engine.get_status("engineering_entity", mount.id) == StalenessStatus.STALE
        assert await engine.get_status("engineering_entity", simulation.id) == StalenessStatus.STALE

    async def test_dependency_on_an_engineering_entity_revision_is_also_tracked(
        self, engine, twin, project_id
    ):
        need = await _seed_evidence(twin, project_id, statement="need statement")
        derived = await _seed_evidence(twin, project_id, statement="derived analysis")
        await engine.declare_dependencies(
            "engineering_entity",
            derived.id,
            [Dependency(entity_kind="engineering_entity", entity_id=need.id, revision=1)],
        )
        await twin.update_engineering_entity(need.id, {"statement": "revised need"})
        markings = await engine.propagate(project_id, "engineering_entity", need.id)
        assert markings[0].entity_id == derived.id

    async def test_reason_names_the_stale_pin(self, engine, twin, project_id):
        req = await _seed_constraint(twin, project_id)
        evidence = await _seed_evidence(twin, project_id)
        await engine.declare_dependencies(
            "engineering_entity",
            evidence.id,
            [Dependency(entity_kind="constraint", entity_id=req.id, revision=1)],
        )
        await twin.update_constraint(req.id, {"message": "x"})
        markings = await engine.propagate(project_id, "constraint", req.id)
        assert f"{req.id}@1" in markings[0].reason
        assert "now at revision 2" in markings[0].reason
