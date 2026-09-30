"""Unit tests for make_baseline_creator / twin.create_baseline (FORGE-405,
follow-up to FORGE-299)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from api_gateway.twin.baseline import make_baseline_creator
from twin_core.api import InMemoryTwinAPI
from twin_core.models.constraint import Constraint
from twin_core.models.engineering_entity import EngineeringEntity
from twin_core.models.enums import AuthorityState, ConstraintSeverity


@pytest.fixture
def twin() -> InMemoryTwinAPI:
    return InMemoryTwinAPI.create()


async def _add_constraint(twin: InMemoryTwinAPI, project_id) -> Constraint:
    return await twin.create_constraint(
        Constraint(
            name="mass_budget",
            expression="mass_kg <= 5.0",
            severity=ConstraintSeverity.ERROR,
            domain="mechanical",
            source="test",
            project_id=project_id,
        )
    )


async def _add_entity(twin: InMemoryTwinAPI, project_id) -> EngineeringEntity:
    return await twin.create_engineering_entity(
        EngineeringEntity(
            entity_type="risk",
            statement="battery may overheat",
            project_id=project_id,
        )
    )


class TestNoMembersRejected:
    async def test_empty_project_raises(self, twin: InMemoryTwinAPI):
        create = make_baseline_creator(twin)
        with pytest.raises(ValueError, match="nothing to baseline"):
            await create(project_id=str(uuid4()), approved_by=["alice"], reason="v1")


class TestCreatesRealBaseline:
    async def test_pins_every_constraint_and_entity(self, twin: InMemoryTwinAPI):
        project_id = uuid4()
        await _add_constraint(twin, project_id)
        await _add_entity(twin, project_id)
        await _add_entity(twin, project_id)

        create = make_baseline_creator(twin)
        result = await create(project_id=str(project_id), approved_by=["alice"], reason="v1")

        assert result["constraint_count"] == 1
        assert result["entity_count"] == 2
        assert result["member_count"] == 3
        assert result["approved_by"] == ["alice"]
        assert result["reason"] == "v1"
        assert result["node_id"]
        assert result["created_at"]

    async def test_default_name_carries_a_timestamp(self, twin: InMemoryTwinAPI):
        project_id = uuid4()
        await _add_entity(twin, project_id)
        create = make_baseline_creator(twin)
        result = await create(project_id=str(project_id), approved_by=["alice"], reason="v1")
        assert result["name"].startswith("Baseline ")

    async def test_explicit_name_is_used(self, twin: InMemoryTwinAPI):
        project_id = uuid4()
        await _add_entity(twin, project_id)
        create = make_baseline_creator(twin)
        result = await create(
            project_id=str(project_id), approved_by=["alice"], reason="v1", name="v1.0"
        )
        assert result["name"] == "v1.0"

    async def test_members_advance_to_baselined_authority(self, twin: InMemoryTwinAPI):
        project_id = uuid4()
        entity = await _add_entity(twin, project_id)
        create = make_baseline_creator(twin)
        await create(project_id=str(project_id), approved_by=["alice"], reason="v1")

        refetched = await twin.get_engineering_entity(entity.id)
        assert refetched is not None
        assert refetched.authority == AuthorityState.BASELINED

    async def test_real_baseline_node_is_queryable_and_satisfies_g8_check(
        self, twin: InMemoryTwinAPI
    ):
        project_id = uuid4()
        await _add_entity(twin, project_id)
        create = make_baseline_creator(twin)
        result = await create(project_id=str(project_id), approved_by=["alice"], reason="v1")

        baselines = await twin.list_baselines(project_id=project_id)
        assert len(baselines) == 1
        assert str(baselines[0].id) == result["node_id"]

    async def test_other_project_is_unaffected(self, twin: InMemoryTwinAPI):
        project_a = uuid4()
        project_b = uuid4()
        await _add_entity(twin, project_a)
        await _add_entity(twin, project_b)
        create = make_baseline_creator(twin)
        await create(project_id=str(project_a), approved_by=["alice"], reason="v1")

        baselines_a = await twin.list_baselines(project_id=project_a)
        baselines_b = await twin.list_baselines(project_id=project_b)
        assert len(baselines_a) == 1
        assert len(baselines_b) == 0

    async def test_a_second_baseline_can_be_created_after_the_first(self, twin: InMemoryTwinAPI):
        project_id = uuid4()
        await _add_entity(twin, project_id)
        create = make_baseline_creator(twin)
        first = await create(project_id=str(project_id), approved_by=["alice"], reason="v1")
        await _add_constraint(twin, project_id)
        second = await create(project_id=str(project_id), approved_by=["bob"], reason="v2")

        assert first["node_id"] != second["node_id"]
        assert second["member_count"] == 2  # the original entity + the new constraint
        baselines = await twin.list_baselines(project_id=project_id)
        assert len(baselines) == 2
