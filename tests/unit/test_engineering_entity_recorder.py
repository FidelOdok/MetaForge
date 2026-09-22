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


class _FakeProjectBackend:
    """Mirrors test_constraint_recorder.py's fake -- same generic
    link_work_product(project_id, wp_id, wp_name, wp_type) contract every
    recorder in this package uses."""

    def __init__(self) -> None:
        self.links: list[tuple[str, str, str, str]] = []

    async def link_work_product(self, project_id: str, wp_id: str, name: str, wp_type: str) -> None:
        self.links.append((project_id, wp_id, name, wp_type))


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
async def test_records_a_budget_entity() -> None:
    """FORGE-73: 'budget'/'invariant' are real entity_types now, recorded
    through this same generic recorder -- twin_core.consistency.gates
    loads them back out via budget_from_entity/invariant_from_entity."""
    twin = InMemoryTwinAPI.create()
    record = make_engineering_entity_recorder(twin)

    out = await record(
        entity_type="budget",
        statement="System mass budget: 5kg total.",
        title="mass_budget",
        extra={"metric": "mass", "unit": "kg", "system_total": 5.0},
        project_id=PROJECT_ID,
    )
    assert out["entity_type"] == "budget"

    stored = await twin.get_engineering_entity(UUID(out["node_id"]))
    assert stored is not None
    assert stored.metadata["system_total"] == 5.0


@pytest.mark.asyncio
async def test_records_an_invariant_entity() -> None:
    twin = InMemoryTwinAPI.create()
    record = make_engineering_entity_recorder(twin)

    out = await record(
        entity_type="invariant",
        statement="Total mass must not exceed 5kg.",
        title="INV-MASS",
        extra={"metric": "mass", "unit": "kg", "limit": 5.0, "comparison": "<="},
        project_id=PROJECT_ID,
    )
    assert out["entity_type"] == "invariant"

    stored = await twin.get_engineering_entity(UUID(out["node_id"]))
    assert stored is not None
    assert stored.metadata["limit"] == 5.0


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


# --- FORGE-48: project linking (the readiness check for G0/G1 depends on this) ---


@pytest.mark.asyncio
async def test_links_the_entity_to_the_project_when_a_backend_is_given() -> None:
    twin = InMemoryTwinAPI.create()
    backend = _FakeProjectBackend()
    record = make_engineering_entity_recorder(twin, backend)

    out = await record(
        entity_type="intent",
        statement="Build a desktop quadruped platform.",
        title="Desktop quadruped intent",
        project_id=PROJECT_ID,
    )
    assert out["project_linked"] is True
    assert backend.links == [(PROJECT_ID, out["node_id"], "Desktop quadruped intent", "intent")]


@pytest.mark.asyncio
async def test_no_link_attempted_without_a_backend() -> None:
    twin = InMemoryTwinAPI.create()
    record = make_engineering_entity_recorder(twin)  # no project_backend
    out = await record(entity_type="intent", statement="x", project_id=PROJECT_ID)
    assert out["project_linked"] is False


@pytest.mark.asyncio
async def test_no_link_attempted_without_a_project_id() -> None:
    twin = InMemoryTwinAPI.create()
    backend = _FakeProjectBackend()
    record = make_engineering_entity_recorder(twin, backend)
    out = await record(entity_type="intent", statement="x")  # no project_id
    assert out["project_linked"] is False
    assert backend.links == []


@pytest.mark.asyncio
async def test_link_falls_back_to_a_truncated_statement_when_no_title_given() -> None:
    twin = InMemoryTwinAPI.create()
    backend = _FakeProjectBackend()
    record = make_engineering_entity_recorder(twin, backend)
    long_statement = "x" * 100
    await record(entity_type="risk", statement=long_statement, project_id=PROJECT_ID)
    assert backend.links[0][2] == long_statement[:60]


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
