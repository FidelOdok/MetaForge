"""Unit tests for GET /v1/decisions (FORGE-289, gap G-G3)."""

from __future__ import annotations

from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient

from api_gateway.twin.decision_recorder import make_decision_recorder
from twin_core.api import InMemoryTwinAPI
from twin_core.models.constraint import Constraint
from twin_core.models.enums import ConstraintSeverity, EdgeType
from twin_core.models.hierarchy_node import HierarchyNode


@pytest.fixture
def twin() -> InMemoryTwinAPI:
    return InMemoryTwinAPI.create()


@pytest.fixture
def app():
    from fastapi import FastAPI

    from api_gateway.twin.decision_routes import router

    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
def client(app):
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture(autouse=True)
def _wire(twin: InMemoryTwinAPI):
    from api_gateway.twin.decision_routes import init_twin

    init_twin(twin)
    yield
    init_twin(InMemoryTwinAPI.create())


async def test_lists_decision_linked_via_parent_refs(client, twin: InMemoryTwinAPI) -> None:
    req = await twin.create_constraint(
        Constraint(
            name="mass_budget",
            expression="True",
            severity=ConstraintSeverity.ERROR,
            domain="mech",
            source="test",
        )
    )
    record = make_decision_recorder(twin, None)
    decision = await record(
        title="D",
        rationale="r",
        alternatives=[{"option": "a", "reason_rejected": "b"}],
        parent_refs=[str(req.id)],
    )

    async with client:
        resp = await client.get("/v1/decisions", params={"related_to": str(req.id)})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["decisions"]) == 1
    assert body["decisions"][0]["id"] == decision["node_id"]
    assert body["decisions"][0]["title"] == "D"
    assert body["decisions"][0]["alternatives"] == [{"option": "a", "reason_rejected": "b"}]


async def test_lists_decision_linked_via_evidence_refs(client, twin: InMemoryTwinAPI) -> None:
    from api_gateway.twin.evidence_recorder import make_evidence_recorder

    record_evidence = make_evidence_recorder(twin, None)
    ev = await record_evidence(
        evidence_type="calculation", producer={"tool": "test"}, inputs={}, result={"y": 1}
    )
    record = make_decision_recorder(twin, None)
    decision = await record(title="D", rationale="r", evidence_refs=[ev["node_id"]])

    async with client:
        resp = await client.get("/v1/decisions", params={"related_to": ev["node_id"]})
    assert resp.status_code == 200
    body = resp.json()
    assert [d["id"] for d in body["decisions"]] == [decision["node_id"]]


async def test_lists_decision_linked_to_hierarchy_node(client, twin: InMemoryTwinAPI) -> None:
    node = await twin.create_hierarchy_node(HierarchyNode(kind="assembly", name="Upper Arm Link"))
    record = make_decision_recorder(twin, None)
    decision = await record(title="D", rationale="r")
    await twin.add_edge(UUID(decision["node_id"]), node.id, EdgeType.SATISFIES)

    async with client:
        resp = await client.get("/v1/decisions", params={"related_to": str(node.id)})
    assert resp.status_code == 200
    assert [d["id"] for d in resp.json()["decisions"]] == [decision["node_id"]]


async def test_ignores_non_decision_sources(client, twin: InMemoryTwinAPI) -> None:
    """A related node can have plenty of other incoming edges (e.g. two
    DesignLoopIteration nodes chained via SUPERSEDES) -- only edges whose
    source is actually a Decision work product should come back."""
    req = await twin.create_constraint(
        Constraint(
            name="mass_budget",
            expression="True",
            severity=ConstraintSeverity.ERROR,
            domain="mech",
            source="test",
        )
    )
    other_req = await twin.create_constraint(
        Constraint(
            name="other",
            expression="True",
            severity=ConstraintSeverity.ERROR,
            domain="mech",
            source="test",
        )
    )
    await twin.add_edge(other_req.id, req.id, EdgeType.DERIVES_FROM)

    async with client:
        resp = await client.get("/v1/decisions", params={"related_to": str(req.id)})
    assert resp.status_code == 200
    assert resp.json()["decisions"] == []


async def test_no_related_decisions_returns_empty_list(client) -> None:
    async with client:
        resp = await client.get(
            "/v1/decisions", params={"related_to": "11111111-1111-1111-1111-111111111111"}
        )
    assert resp.status_code == 200
    assert resp.json()["decisions"] == []


async def test_invalid_uuid_400s(client) -> None:
    async with client:
        resp = await client.get("/v1/decisions", params={"related_to": "not-a-uuid"})
    assert resp.status_code == 400
