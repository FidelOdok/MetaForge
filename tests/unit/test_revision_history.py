"""Unit tests for revision-history queryability (FORGE-51):
TwinAPI.get_constraint_revision / get_engineering_entity_revision.
"""

from uuid import uuid4

import pytest

from twin_core.api import InMemoryTwinAPI
from twin_core.models.constraint import Constraint
from twin_core.models.engineering_entity import EngineeringEntity
from twin_core.models.enums import ConstraintSeverity


@pytest.fixture
def twin():
    return InMemoryTwinAPI.create()


class TestConstraintRevisionHistory:
    async def test_current_revision_returns_live_object(self, twin):
        c = Constraint(
            name="payload",
            expression="m<=3",
            severity=ConstraintSeverity.ERROR,
            domain="mech",
            source="test",
        )
        await twin.create_constraint(c)
        fetched = await twin.get_constraint_revision(c.id, 1)
        assert fetched is not None
        assert fetched.revision == 1
        assert fetched.expression == "m<=3"

    async def test_past_revision_reconstructs_from_snapshot(self, twin):
        c = Constraint(
            name="payload",
            expression="m<=3",
            severity=ConstraintSeverity.ERROR,
            domain="mech",
            source="test",
        )
        await twin.create_constraint(c)
        await twin.update_constraint(c.id, {"expression": "m<=4"}, expected_revision=1)
        await twin.update_constraint(c.id, {"expression": "m<=5"}, expected_revision=2)

        rev1 = await twin.get_constraint_revision(c.id, 1)
        rev2 = await twin.get_constraint_revision(c.id, 2)
        rev3 = await twin.get_constraint_revision(c.id, 3)

        assert rev1.expression == "m<=3"
        assert rev2.expression == "m<=4"
        assert rev3.expression == "m<=5"  # current
        assert rev3.revision == 3

    async def test_unknown_revision_returns_none(self, twin):
        c = Constraint(
            name="payload",
            expression="m<=3",
            severity=ConstraintSeverity.ERROR,
            domain="mech",
            source="test",
        )
        await twin.create_constraint(c)
        assert await twin.get_constraint_revision(c.id, 99) is None

    async def test_unknown_entity_returns_none(self, twin):
        assert await twin.get_constraint_revision(uuid4(), 1) is None


class TestEngineeringEntityRevisionHistory:
    async def test_past_revision_reconstructs_from_snapshot(self, twin):
        e = EngineeringEntity(entity_type="intent", statement="v1")
        await twin.create_engineering_entity(e)
        await twin.update_engineering_entity(e.id, {"statement": "v2"}, expected_revision=1)

        rev1 = await twin.get_engineering_entity_revision(e.id, 1)
        rev2 = await twin.get_engineering_entity_revision(e.id, 2)

        assert rev1.statement == "v1"
        assert rev2.statement == "v2"

    async def test_snapshots_are_not_listed_as_engineering_entities(self, twin):
        e = EngineeringEntity(entity_type="intent", statement="v1")
        await twin.create_engineering_entity(e)
        await twin.update_engineering_entity(e.id, {"statement": "v2"}, expected_revision=1)

        listed = await twin.list_engineering_entities()
        assert len(listed) == 1
        assert listed[0].statement == "v2"
