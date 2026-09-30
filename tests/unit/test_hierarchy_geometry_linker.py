"""api_gateway/twin/hierarchy_recorder.py's make_hierarchy_geometry_linker
(FORGE-266, gap G-C2) -- "replace placeholder with part": attach or replace
a HierarchyNode's REALIZED_BY/INSTANCE_OF geometry after the node already
exists.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from api_gateway.twin.hierarchy_recorder import make_hierarchy_geometry_linker
from tool_registry.tools.twin.adapter import TwinServer
from twin_core.api import InMemoryTwinAPI
from twin_core.models.bom_item import BOMItem
from twin_core.models.enums import EdgeType, WorkProductType
from twin_core.models.hierarchy_node import HierarchyNode
from twin_core.models.work_product import WorkProduct


@pytest.fixture
def twin():
    return InMemoryTwinAPI.create()


async def _cad_model(twin, name: str = "Bracket") -> WorkProduct:
    return await twin.create_work_product(
        WorkProduct(
            name=name,
            type=WorkProductType.CAD_MODEL,
            domain="mechanical",
            file_path="",
            content_hash="deadbeef",
            format="step",
            created_by="test",
        )
    )


async def _bom_item(twin, part_number: str = "DS3218MG") -> BOMItem:
    return await twin.add_bom_item(BOMItem(part_number=part_number, manufacturer="Miuzei"))


class TestMakeHierarchyGeometryLinker:
    async def test_sets_realized_by_on_a_placeholder_node(self, twin):
        node = await twin.create_hierarchy_node(HierarchyNode(name="Bracket slot", kind="assembly"))
        cad = await _cad_model(twin)
        realize = make_hierarchy_geometry_linker(twin)

        result = await realize(hierarchy_node_id=str(node.id), work_product_id=str(cad.id))

        assert result["realized_by_work_product_id"] == str(cad.id)
        assert result["instance_of_bom_item_id"] is None
        edges = await twin.get_edges(node.id, direction="outgoing", edge_type=EdgeType.REALIZED_BY)
        assert len(edges) == 1
        assert edges[0].target_id == cad.id

    async def test_sets_instance_of_on_a_placeholder_node(self, twin):
        node = await twin.create_hierarchy_node(
            HierarchyNode(name="Actuator slot", kind="assembly")
        )
        bom = await _bom_item(twin)
        realize = make_hierarchy_geometry_linker(twin)

        result = await realize(hierarchy_node_id=str(node.id), bom_item_id=str(bom.id))

        assert result["instance_of_bom_item_id"] == str(bom.id)
        edges = await twin.get_edges(node.id, direction="outgoing", edge_type=EdgeType.INSTANCE_OF)
        assert len(edges) == 1
        assert edges[0].target_id == bom.id

    async def test_can_set_both_in_one_call(self, twin):
        node = await twin.create_hierarchy_node(
            HierarchyNode(name="Actuator slot", kind="assembly")
        )
        cad = await _cad_model(twin)
        bom = await _bom_item(twin)
        realize = make_hierarchy_geometry_linker(twin)

        result = await realize(
            hierarchy_node_id=str(node.id), work_product_id=str(cad.id), bom_item_id=str(bom.id)
        )

        assert result["realized_by_work_product_id"] == str(cad.id)
        assert result["instance_of_bom_item_id"] == str(bom.id)

    async def test_replaces_an_existing_realized_by_edge_not_accumulates(self, twin):
        node = await twin.create_hierarchy_node(HierarchyNode(name="Bracket slot", kind="assembly"))
        old_cad = await _cad_model(twin, "Old bracket v1")
        new_cad = await _cad_model(twin, "New bracket v2")
        realize = make_hierarchy_geometry_linker(twin)
        await realize(hierarchy_node_id=str(node.id), work_product_id=str(old_cad.id))

        result = await realize(hierarchy_node_id=str(node.id), work_product_id=str(new_cad.id))

        assert result["realized_by_work_product_id"] == str(new_cad.id)
        edges = await twin.get_edges(node.id, direction="outgoing", edge_type=EdgeType.REALIZED_BY)
        assert len(edges) == 1
        assert edges[0].target_id == new_cad.id

    async def test_replaces_an_existing_instance_of_edge_not_accumulates(self, twin):
        node = await twin.create_hierarchy_node(
            HierarchyNode(name="Actuator slot", kind="assembly")
        )
        old_bom = await _bom_item(twin, "MG996R")
        new_bom = await _bom_item(twin, "DS3218MG")
        realize = make_hierarchy_geometry_linker(twin)
        await realize(hierarchy_node_id=str(node.id), bom_item_id=str(old_bom.id))

        result = await realize(hierarchy_node_id=str(node.id), bom_item_id=str(new_bom.id))

        assert result["instance_of_bom_item_id"] == str(new_bom.id)
        edges = await twin.get_edges(node.id, direction="outgoing", edge_type=EdgeType.INSTANCE_OF)
        assert len(edges) == 1
        assert edges[0].target_id == new_bom.id

    async def test_requires_at_least_one_target(self, twin):
        node = await twin.create_hierarchy_node(HierarchyNode(name="x", kind="assembly"))
        realize = make_hierarchy_geometry_linker(twin)
        with pytest.raises(ValueError, match="work_product_id"):
            await realize(hierarchy_node_id=str(node.id))

    async def test_unknown_hierarchy_node_raises(self, twin):
        cad = await _cad_model(twin)
        realize = make_hierarchy_geometry_linker(twin)
        with pytest.raises(ValueError, match="hierarchy node"):
            await realize(hierarchy_node_id=str(uuid4()), work_product_id=str(cad.id))

    async def test_unknown_work_product_raises(self, twin):
        node = await twin.create_hierarchy_node(HierarchyNode(name="x", kind="assembly"))
        realize = make_hierarchy_geometry_linker(twin)
        with pytest.raises(ValueError, match="work product"):
            await realize(hierarchy_node_id=str(node.id), work_product_id=str(uuid4()))

    async def test_unknown_bom_item_raises(self, twin):
        node = await twin.create_hierarchy_node(HierarchyNode(name="x", kind="assembly"))
        realize = make_hierarchy_geometry_linker(twin)
        with pytest.raises(ValueError, match="BOMItem"):
            await realize(hierarchy_node_id=str(node.id), bom_item_id=str(uuid4()))

    async def test_non_bom_item_node_id_raises(self, twin):
        node = await twin.create_hierarchy_node(HierarchyNode(name="x", kind="assembly"))
        cad = await _cad_model(twin)
        realize = make_hierarchy_geometry_linker(twin)
        with pytest.raises(ValueError, match="BOMItem"):
            await realize(hierarchy_node_id=str(node.id), bom_item_id=str(cad.id))

    async def test_realized_geometry_feeds_the_hierarchical_bom(self, twin):
        """Integration proof: a node realized here shows up in FORGE-267's
        EBOM derivation with zero further changes, since both read the SAME
        REALIZED_BY/INSTANCE_OF edges."""
        from twin_core.consistency.hierarchical_bom import compute_hierarchical_bom

        root = await twin.create_hierarchy_node(HierarchyNode(name="Arm", kind="product"))
        leaf = await twin.create_hierarchy_node(
            HierarchyNode(name="Elbow actuator", kind="assembly")
        )
        await twin.add_edge(root.id, leaf.id, EdgeType.CONTAINS, {"quantity": 1})
        bom = await _bom_item(twin)
        realize = make_hierarchy_geometry_linker(twin)

        await realize(hierarchy_node_id=str(leaf.id), bom_item_id=str(bom.id))

        lines = await compute_hierarchical_bom(twin, root.id)
        assert len(lines) == 1
        assert lines[0].source == "instance_of"
        assert lines[0].component_id == bom.id


class TestRealizeHierarchyNodeAdapter:
    async def test_tool_registered_and_returns_shape(self, twin):
        node = await twin.create_hierarchy_node(HierarchyNode(name="x", kind="assembly"))
        cad = await _cad_model(twin)
        server = TwinServer(
            twin=twin, hierarchy_geometry_linker=make_hierarchy_geometry_linker(twin)
        )
        assert "twin.realize_hierarchy_node" in server.tool_ids

        out = await server.realize_hierarchy_node(
            {"hierarchy_node_id": str(node.id), "work_product_id": str(cad.id)}
        )
        assert out["realized_by_work_product_id"] == str(cad.id)

    def test_absent_without_linker(self, twin):
        server = TwinServer(twin=twin)
        assert "twin.realize_hierarchy_node" not in server.tool_ids

    async def test_requires_hierarchy_node_id(self, twin):
        server = TwinServer(
            twin=twin, hierarchy_geometry_linker=make_hierarchy_geometry_linker(twin)
        )
        with pytest.raises(ValueError, match="hierarchy_node_id"):
            await server.realize_hierarchy_node({"work_product_id": str(uuid4())})
