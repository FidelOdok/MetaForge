"""twin.record_engineering_entity — Intent & Requirements Harness intake
into the twin (FORGE-45, epic FORGE-35).
"""

from __future__ import annotations

from uuid import UUID

import pytest

from api_gateway.twin.engineering_entity_recorder import make_engineering_entity_recorder
from twin_core.api import InMemoryTwinAPI
from twin_core.models.enums import EdgeType

PROJECT_ID = "11111111-1111-4111-8111-111111111111"


@pytest.mark.asyncio
async def test_records_an_entity_with_no_parents() -> None:
    twin = InMemoryTwinAPI.create()
    record = make_engineering_entity_recorder(twin)

    out = await record(
        entity_type="intent",
        statement="Build a desktop quadruped platform for indoor demos.",
        title="Desktop quadruped intent",
        project_id=PROJECT_ID,
    )
    assert out["entity_type"] == "intent"
    assert out["parent_ids"] == []

    stored = await twin.get_engineering_entity(UUID(out["node_id"]))
    assert stored is not None
    assert stored.title == "Desktop quadruped intent"
    assert stored.project_id == UUID(PROJECT_ID)


@pytest.mark.asyncio
async def test_records_an_entity_linked_to_a_parent_by_name() -> None:
    twin = InMemoryTwinAPI.create()
    record = make_engineering_entity_recorder(twin)

    parent = await record(
        entity_type="intent",
        statement="Build a desktop quadruped platform.",
        title="Desktop quadruped intent",
        project_id=PROJECT_ID,
    )
    child = await record(
        entity_type="stakeholder_need",
        statement="The operator needs the robot to operate safely indoors.",
        title="Operator safety need",
        parent_refs=["Desktop quadruped intent"],
        relation="motivates",
        project_id=PROJECT_ID,
    )
    assert child["parent_ids"] == [parent["node_id"]]

    stored = await twin.get_engineering_entity(UUID(child["node_id"]))
    assert stored is not None
    assert stored.parent_refs == [parent["node_id"]]

    edges = await twin.get_edges(UUID(child["node_id"]), edge_type=EdgeType.MOTIVATES)
    assert len(edges) == 1
    assert str(edges[0].target_id) == parent["node_id"]


@pytest.mark.asyncio
async def test_records_an_entity_linked_to_a_parent_by_uuid() -> None:
    twin = InMemoryTwinAPI.create()
    record = make_engineering_entity_recorder(twin)

    parent = await record(entity_type="intent", statement="x", project_id=PROJECT_ID)
    child = await record(
        entity_type="objective",
        statement="Minimize total mass.",
        parent_refs=[parent["node_id"]],
        project_id=PROJECT_ID,
    )
    assert child["parent_ids"] == [parent["node_id"]]


@pytest.mark.asyncio
async def test_default_relation_is_derives_from() -> None:
    twin = InMemoryTwinAPI.create()
    record = make_engineering_entity_recorder(twin)

    parent = await record(entity_type="intent", statement="x", project_id=PROJECT_ID)
    child = await record(
        entity_type="objective",
        statement="y",
        parent_refs=[parent["node_id"]],
        project_id=PROJECT_ID,
    )
    edges = await twin.get_edges(UUID(child["node_id"]), edge_type=EdgeType.DERIVES_FROM)
    assert len(edges) == 1


@pytest.mark.asyncio
async def test_invalid_entity_type_rejected() -> None:
    twin = InMemoryTwinAPI.create()
    record = make_engineering_entity_recorder(twin)
    with pytest.raises(ValueError, match="entity_type"):
        await record(entity_type="not_a_real_type", statement="x")


@pytest.mark.asyncio
async def test_empty_statement_rejected() -> None:
    twin = InMemoryTwinAPI.create()
    record = make_engineering_entity_recorder(twin)
    with pytest.raises(ValueError, match="statement"):
        await record(entity_type="intent", statement="")


@pytest.mark.asyncio
async def test_invalid_relation_rejected() -> None:
    twin = InMemoryTwinAPI.create()
    record = make_engineering_entity_recorder(twin)
    parent = await record(entity_type="intent", statement="x", project_id=PROJECT_ID)
    with pytest.raises(ValueError, match="relation"):
        await record(
            entity_type="objective",
            statement="y",
            parent_refs=[parent["node_id"]],
            relation="not_a_real_edge_type",
            project_id=PROJECT_ID,
        )


@pytest.mark.asyncio
async def test_unresolvable_parent_ref_raises_and_does_not_create_the_entity() -> None:
    twin = InMemoryTwinAPI.create()
    record = make_engineering_entity_recorder(twin)
    with pytest.raises(ValueError, match="did not resolve"):
        await record(
            entity_type="objective",
            statement="y",
            parent_refs=["nonexistent"],
            project_id=PROJECT_ID,
        )
    # Loud failure must not leave a half-created orphan node behind.
    assert await twin.list_engineering_entities(project_id=UUID(PROJECT_ID)) == []


@pytest.mark.asyncio
async def test_extra_and_session_id_land_in_metadata() -> None:
    twin = InMemoryTwinAPI.create()
    record = make_engineering_entity_recorder(twin)
    out = await record(
        entity_type="risk",
        statement="Actuator torque may be insufficient.",
        extra={"probability": "medium", "severity": "high"},
        session_id="sess-1",
        project_id=PROJECT_ID,
    )
    stored = await twin.get_engineering_entity(UUID(out["node_id"]))
    assert stored is not None
    assert stored.metadata["probability"] == "medium"
    assert stored.metadata["session_id"] == "sess-1"
