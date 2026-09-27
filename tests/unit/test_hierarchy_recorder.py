"""api_gateway/twin/hierarchy_recorder.py (FORGE-260, gap G-B1) --
make_hierarchy_node_recorder + make_hierarchy_rollup_fn, and the twin
adapter handlers that call them.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from api_gateway.twin.hierarchy_recorder import (
    make_hierarchy_node_recorder,
    make_hierarchy_rollup_fn,
)
from tool_registry.tools.twin.adapter import TwinServer
from twin_core.api import InMemoryTwinAPI
from twin_core.models.enums import EdgeType, WorkProductType
from twin_core.models.hierarchy_node import HierarchyNode
from twin_core.models.work_product import WorkProduct


@pytest.fixture
def twin():
    return InMemoryTwinAPI.create()


class TestHierarchyNodeRecorder:
    async def test_creates_a_real_hierarchy_node(self, twin):
        record = make_hierarchy_node_recorder(twin, None)
        result = await record(name="6-DOF Arm", kind="product")

        assert result["parent_linked"] is False
        node = await twin.get_hierarchy_node(UUID(result["node_id"]))
        assert node is not None
        assert node.kind == "product"
        assert node.name == "6-DOF Arm"

    async def test_rejects_unknown_kind(self, twin):
        record = make_hierarchy_node_recorder(twin, None)
        with pytest.raises(ValueError, match="kind"):
            await record(name="x", kind="galaxy")

    async def test_parent_id_creates_contains_edge_with_quantity(self, twin):
        record = make_hierarchy_node_recorder(twin, None)
        parent = await twin.create_hierarchy_node(HierarchyNode(name="Base", kind="subsystem"))

        result = await record(
            name="Standoff", kind="assembly", parent_id=str(parent.id), quantity=4
        )
        assert result["parent_linked"] is True
        edges = await twin.get_edges(parent.id)
        contains = [e for e in edges if e.edge_type == EdgeType.CONTAINS]
        assert len(contains) == 1
        assert contains[0].target_id == UUID(result["node_id"])
        assert contains[0].metadata["quantity"] == 4

    async def test_placement_is_stored_on_the_contains_edge(self, twin):
        record = make_hierarchy_node_recorder(twin, None)
        parent = await twin.create_hierarchy_node(HierarchyNode(name="Base", kind="subsystem"))
        result = await record(
            name="Standoff",
            kind="assembly",
            parent_id=str(parent.id),
            placement={"x": 10, "y": 0, "z": 5},
        )
        edges = await twin.get_edges(parent.id)
        contains = [e for e in edges if e.edge_type == EdgeType.CONTAINS][0]
        assert contains.metadata["placement"] == {"x": 10, "y": 0, "z": 5}
        assert result["parent_linked"] is True

    async def test_a_bad_parent_id_does_not_block_the_commit(self, twin):
        record = make_hierarchy_node_recorder(twin, None)
        result = await record(name="Standoff", kind="assembly", parent_id=str(uuid4()))
        assert result["node_id"]
        assert result["parent_linked"] is False

    async def test_realized_by_node_id_creates_the_edge(self, twin):
        record = make_hierarchy_node_recorder(twin, None)
        cad = await twin.create_work_product(
            WorkProduct(
                name="Bracket",
                type=WorkProductType.CAD_MODEL,
                domain="mechanical",
                file_path="",
                content_hash="deadbeef",
                format="step",
                created_by="test",
            )
        )
        result = await record(name="Bracket slot", kind="assembly", realized_by_node_id=str(cad.id))
        assert result["realized_by_linked"] is True
        edges = await twin.get_edges(UUID(result["node_id"]))
        assert any(e.target_id == cad.id and e.edge_type == EdgeType.REALIZED_BY for e in edges)

    async def test_instance_of_node_id_creates_the_edge(self, twin):
        from twin_core.models.bom_item import BOMItem

        record = make_hierarchy_node_recorder(twin, None)
        bom = await twin.add_bom_item(BOMItem(part_number="igus RL-SE-80-50", manufacturer="igus"))
        result = await record(
            name="Actuator slot", kind="assembly", instance_of_node_id=str(bom.id)
        )
        assert result["instance_of_linked"] is True
        edges = await twin.get_edges(UUID(result["node_id"]))
        assert any(e.target_id == bom.id and e.edge_type == EdgeType.INSTANCE_OF for e in edges)

    async def test_metadata_is_stored(self, twin):
        record = make_hierarchy_node_recorder(twin, None)
        result = await record(name="x", kind="product", metadata={"maturity": "concept"})
        node = await twin.get_hierarchy_node(UUID(result["node_id"]))
        assert node.metadata["maturity"] == "concept"

    async def test_requires_name(self, twin):
        record = make_hierarchy_node_recorder(twin, None)
        with pytest.raises(ValueError, match="name"):
            await record(name="", kind="product")


class TestHierarchyRollupFn:
    async def test_returns_a_plain_dict(self, twin):
        rollup = make_hierarchy_rollup_fn(twin)
        root = await twin.create_hierarchy_node(HierarchyNode(name="Arm", kind="product"))
        result = await rollup(str(root.id))
        assert isinstance(result, dict)
        assert result["root_id"] == str(root.id)
        assert result["mass_kg"] == 0.0

    async def test_unknown_root_raises(self, twin):
        rollup = make_hierarchy_rollup_fn(twin)
        with pytest.raises(KeyError):
            await rollup(str(uuid4()))


class TestAdapterHandlers:
    async def test_record_hierarchy_node_tool_registered_and_calls_recorder(self, twin):
        server = TwinServer(twin=twin, hierarchy_node_recorder=make_hierarchy_node_recorder(twin))
        assert "twin.record_hierarchy_node" in server.tool_ids
        out = await server.record_hierarchy_node({"name": "Arm", "kind": "product"})
        assert out["node_id"]

    async def test_record_hierarchy_node_absent_without_recorder(self, twin):
        server = TwinServer(twin=twin)
        assert "twin.record_hierarchy_node" not in server.tool_ids

    async def test_record_hierarchy_node_validates_required_fields(self, twin):
        server = TwinServer(twin=twin, hierarchy_node_recorder=make_hierarchy_node_recorder(twin))
        with pytest.raises(ValueError, match="name"):
            await server.record_hierarchy_node({"kind": "product"})
        with pytest.raises(ValueError, match="kind"):
            await server.record_hierarchy_node({"name": "x"})

    async def test_compute_hierarchy_rollup_tool_registered_and_calls_fn(self, twin):
        server = TwinServer(twin=twin, hierarchy_rollup_fn=make_hierarchy_rollup_fn(twin))
        assert "twin.compute_hierarchy_rollup" in server.tool_ids
        root = await twin.create_hierarchy_node(HierarchyNode(name="Arm", kind="product"))
        out = await server.compute_hierarchy_rollup({"root_id": str(root.id)})
        assert out["root_id"] == str(root.id)

    async def test_compute_hierarchy_rollup_absent_without_fn(self, twin):
        server = TwinServer(twin=twin)
        assert "twin.compute_hierarchy_rollup" not in server.tool_ids

    async def test_compute_hierarchy_rollup_requires_root_id(self, twin):
        server = TwinServer(twin=twin, hierarchy_rollup_fn=make_hierarchy_rollup_fn(twin))
        with pytest.raises(ValueError, match="root_id"):
            await server.compute_hierarchy_rollup({})
