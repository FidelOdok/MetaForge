"""Unit tests for Baseline creation (FORGE-51):
twin_core.transactions.baseline.create_baseline.
"""

from uuid import uuid4

import pytest

from twin_core.api import InMemoryTwinAPI
from twin_core.models.constraint import Constraint
from twin_core.models.engineering_entity import EngineeringEntity
from twin_core.models.enums import AuthorityState, ConstraintSeverity
from twin_core.transactions.baseline import create_baseline
from twin_core.transactions.engine import TransactionEngine


@pytest.fixture
def twin():
    return InMemoryTwinAPI.create()


@pytest.fixture
def engine(twin):
    return TransactionEngine(twin)


async def _seed_constraint(twin, name="req") -> Constraint:
    c = Constraint(
        name=name,
        expression="m<=3",
        severity=ConstraintSeverity.ERROR,
        domain="mech",
        source="test",
    )
    return await twin.create_constraint(c)


async def _seed_entity(twin) -> EngineeringEntity:
    e = EngineeringEntity(entity_type="intent", statement="seed")
    return await twin.create_engineering_entity(e)


class TestCreateBaseline:
    async def test_requires_at_least_one_member(self, twin, engine):
        with pytest.raises(ValueError, match="at least one member"):
            await create_baseline(
                twin, engine, name="BL-1", members=[], approved_by=["user:1"], reason="x"
            )

    async def test_requires_approved_by(self, twin, engine):
        c = await _seed_constraint(twin)
        with pytest.raises(ValueError, match="approved_by is required"):
            await create_baseline(
                twin,
                engine,
                name="BL-1",
                members=[("constraint", c.id)],
                approved_by=[],
                reason="x",
            )

    async def test_creates_baseline_and_bumps_authority(self, twin, engine):
        c = await _seed_constraint(twin)
        e = await _seed_entity(twin)

        result = await create_baseline(
            twin,
            engine,
            name="BL-SYSREQ-001",
            members=[("constraint", c.id), ("engineering_entity", e.id)],
            approved_by=["user:123"],
            reason="Initial system baseline",
        )

        assert result.status == "created"
        assert result.baseline.name == "BL-SYSREQ-001"
        assert len(result.baseline.includes) == 2

        fetched_c = await twin.get_constraint(c.id)
        fetched_e = await twin.get_engineering_entity(e.id)
        assert fetched_c.authority == AuthorityState.BASELINED
        assert fetched_e.authority == AuthorityState.BASELINED
        # Baselining is itself a revision-creating write (see module
        # docstring) -- the pin records the post-bump revision.
        assert fetched_c.revision == 2
        assert fetched_e.revision == 2
        member_revisions = {m.entity_id: m.revision for m in result.baseline.includes}
        assert member_revisions[c.id] == 2
        assert member_revisions[e.id] == 2

    async def test_links_members_via_included_in_baseline_edge(self, twin, engine):
        c = await _seed_constraint(twin)
        result = await create_baseline(
            twin,
            engine,
            name="BL-1",
            members=[("constraint", c.id)],
            approved_by=["user:1"],
            reason="x",
        )
        edges = await twin.get_edges(c.id, direction="outgoing")
        assert any(e.target_id == result.baseline.id for e in edges)

    async def test_missing_member_is_a_conflict_with_no_baseline_created(self, twin, engine):
        result = await create_baseline(
            twin,
            engine,
            name="BL-1",
            members=[("constraint", uuid4())],
            approved_by=["user:1"],
            reason="x",
        )
        assert result.status == "conflict"
        assert result.baseline is None
        assert await twin.list_baselines() == []

    async def test_baselines_the_true_current_revision_after_intervening_edits(self, twin, engine):
        c = await _seed_constraint(twin)
        await twin.update_constraint(c.id, {"message": "edited"}, expected_revision=1)
        assert (await twin.get_constraint(c.id)).revision == 2

        result = await create_baseline(
            twin,
            engine,
            name="BL-1",
            members=[("constraint", c.id)],
            approved_by=["user:1"],
            reason="x",
        )
        assert result.status == "created"
        # Reads fresh internally -- never baselines stale data even when
        # the caller passed the entity id well before any read happened.
        assert result.baseline.includes[0].revision == 3

    async def test_multi_member_zero_partial_writes_on_conflict(self, twin, engine):
        c = await _seed_constraint(twin)
        missing_id = uuid4()

        result = await create_baseline(
            twin,
            engine,
            name="BL-1",
            members=[("constraint", c.id), ("constraint", missing_id)],
            approved_by=["user:1"],
            reason="x",
        )
        assert result.status == "conflict"
        # The valid member must NOT have had its authority bumped either.
        fetched = await twin.get_constraint(c.id)
        assert fetched.authority == AuthorityState.PROPOSED
        assert fetched.revision == 1
