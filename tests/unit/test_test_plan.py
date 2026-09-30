"""Unit tests for make_test_plan_generator / make_test_plan_lister /
twin.generate_test_plan (FORGE-298)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from api_gateway.twin.engineering_entity_recorder import make_engineering_entity_recorder
from api_gateway.twin.test_plan import make_test_plan_generator, make_test_plan_lister
from twin_core.api import InMemoryTwinAPI
from twin_core.models.constraint import Constraint
from twin_core.models.enums import ConstraintSeverity


@pytest.fixture
def twin() -> InMemoryTwinAPI:
    return InMemoryTwinAPI.create()


def _generator(twin: InMemoryTwinAPI):
    recorder = make_engineering_entity_recorder(twin)
    return make_test_plan_generator(twin, engineering_entity_recorder=recorder)


async def _make_constraint(
    twin: InMemoryTwinAPI,
    project_id,
    *,
    name: str,
    verification_method: str,
    metric: str = "payload_capacity_kg",
    operator: str = ">=",
    limit: float = 2.0,
    unit: str = "kg",
    target_node_type: str = "robot_description",
) -> Constraint:
    constraint = Constraint(
        name=name,
        expression=f"{metric} {operator} {limit}",
        severity=ConstraintSeverity.ERROR,
        domain="mechanical",
        source="test",
        verification_method=verification_method,
        metric=metric,
        operator=operator,
        limit=limit,
        unit=unit,
        target_node_type=target_node_type,
        project_id=project_id,
    )
    return await twin.create_constraint(constraint)


class TestGenerateTestPlan:
    async def test_no_requirements_returns_empty(self, twin: InMemoryTwinAPI):
        generate = _generator(twin)
        project_id = uuid4()
        result = await generate(project_id=str(project_id))
        assert result["entries"] == []

    async def test_non_test_verification_method_is_skipped(self, twin: InMemoryTwinAPI):
        project_id = uuid4()
        await _make_constraint(twin, project_id, name="tip_deflection", verification_method="FEA")
        generate = _generator(twin)
        result = await generate(project_id=str(project_id))
        assert result["entries"] == []

    async def test_test_method_requirement_generates_one_entry(self, twin: InMemoryTwinAPI):
        project_id = uuid4()
        await _make_constraint(
            twin,
            project_id,
            name="payload_capacity",
            verification_method="test",
            metric="payload_capacity_kg",
            operator=">=",
            limit=2.0,
            unit="kg",
            target_node_type="robot_description",
        )
        generate = _generator(twin)
        result = await generate(project_id=str(project_id))
        assert len(result["entries"]) == 1
        entry = result["entries"][0]
        assert entry["step"] == (
            "Measure payload_capacity_kg on robot_description; "
            "acceptance: payload_capacity_kg >= 2.0kg"
        )
        assert entry["acceptance_value"] == "2.0kg"
        assert entry["requirement_name"] == "payload_capacity"

    async def test_mixed_requirements_only_generates_for_test_method(self, twin: InMemoryTwinAPI):
        project_id = uuid4()
        await _make_constraint(twin, project_id, name="tip_deflection", verification_method="FEA")
        await _make_constraint(
            twin,
            project_id,
            name="reach_envelope",
            verification_method="test",
            metric="reach_mm",
            operator="<=",
            limit=650.0,
            unit="mm",
            target_node_type="robot_description",
        )
        generate = _generator(twin)
        result = await generate(project_id=str(project_id))
        assert len(result["entries"]) == 1
        assert result["entries"][0]["requirement_name"] == "reach_envelope"

    async def test_recorded_entity_is_real_verification_case_with_metadata(
        self, twin: InMemoryTwinAPI
    ):
        project_id = uuid4()
        constraint = await _make_constraint(
            twin,
            project_id,
            name="repeatability",
            verification_method="test",
            metric="repeatability_mm",
            operator="<=",
            limit=0.5,
            unit="mm",
            target_node_type="robot_description",
        )
        generate = _generator(twin)
        result = await generate(project_id=str(project_id))
        node_id = result["entries"][0]["node_id"]
        entities = await twin.list_engineering_entities(
            project_id=project_id, entity_type="verification_case"
        )
        assert len(entities) == 1
        entity = entities[0]
        assert str(entity.id) == node_id
        assert entity.metadata["requirement_id"] == str(constraint.id)
        assert entity.metadata["acceptance_value"] == "0.5mm"
        assert "repeatability_mm" in entity.metadata["step"]


class TestListTestPlan:
    async def test_lists_generated_entries_oldest_first(self, twin: InMemoryTwinAPI):
        project_id = uuid4()
        await _make_constraint(
            twin,
            project_id,
            name="payload_capacity",
            verification_method="test",
        )
        generate = _generator(twin)
        await generate(project_id=str(project_id))

        list_entries = make_test_plan_lister(twin)
        entries = await list_entries(project_id=str(project_id))
        assert len(entries) == 1
        assert entries[0]["step"].startswith("Measure payload_capacity_kg")
        assert entries[0]["acceptance_value"] == "2.0kg"

    async def test_empty_project_lists_nothing(self, twin: InMemoryTwinAPI):
        list_entries = make_test_plan_lister(twin)
        entries = await list_entries(project_id=str(uuid4()))
        assert entries == []
