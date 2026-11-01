"""compute_hierarchical_bom (FORGE-267, gap G-C3) -- EBOM derived from the
product hierarchy, with quantities multiplied down the tree.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from twin_core.api import InMemoryTwinAPI
from twin_core.consistency.hierarchical_bom import compute_hierarchical_bom
from twin_core.models.bom_item import BOMItem
from twin_core.models.enums import EdgeType, WorkProductType
from twin_core.models.hierarchy_node import HierarchyNode
from twin_core.models.work_product import WorkProduct


@pytest.fixture
def api():
    return InMemoryTwinAPI.create()


def _cad_model(name: str) -> WorkProduct:
    return WorkProduct(
        name=name,
        type=WorkProductType.CAD_MODEL,
        domain="mechanical",
        file_path="",
        content_hash="deadbeef",
        format="step",
        created_by="test",
    )


class TestComputeHierarchicalBom:
    async def test_empty_tree_has_no_lines(self, api):
        root = await api.create_hierarchy_node(HierarchyNode(name="Arm", kind="product"))
        lines = await compute_hierarchical_bom(api, root.id)
        assert lines == []

    async def test_realized_by_leaf_is_one_line_with_quantity_one(self, api):
        root = await api.create_hierarchy_node(HierarchyNode(name="Arm", kind="product"))
        base = await api.create_hierarchy_node(HierarchyNode(name="Base", kind="subsystem"))
        await api.add_edge(root.id, base.id, EdgeType.CONTAINS, {"quantity": 1})
        part = await api.create_work_product(_cad_model("Base plate"))
        await api.add_edge(base.id, part.id, EdgeType.REALIZED_BY)

        lines = await compute_hierarchical_bom(api, root.id)
        assert len(lines) == 1
        assert lines[0].path == ["Arm", "Base"]
        assert lines[0].quantity == 1
        assert lines[0].source == "realized_by"
        assert lines[0].component_id == part.id
        assert lines[0].description == "Base plate"

    async def test_instance_of_leaf_carries_bom_item_fields(self, api):
        root = await api.create_hierarchy_node(HierarchyNode(name="Arm", kind="product"))
        actuator_slot = await api.create_hierarchy_node(
            HierarchyNode(name="Actuator", kind="assembly")
        )
        await api.add_edge(root.id, actuator_slot.id, EdgeType.CONTAINS, {"quantity": 1})
        bom = await api.add_bom_item(
            BOMItem(
                part_number="RL-SE-80-50",
                manufacturer="igus",
                description="Slewing ring actuator",
                unit_cost=42.0,
            )
        )
        await api.add_edge(actuator_slot.id, bom.id, EdgeType.INSTANCE_OF)

        lines = await compute_hierarchical_bom(api, root.id)
        assert len(lines) == 1
        line = lines[0]
        assert line.source == "instance_of"
        assert line.part_number == "RL-SE-80-50"
        assert line.manufacturer == "igus"
        assert line.unit_cost == 42.0

    async def test_quantity_multiplies_down_the_tree(self, api):
        """The ticket's own worked example: 3 identical actuators nested
        inside 2 identical wrist assemblies -- 6 total, not 3."""
        root = await api.create_hierarchy_node(HierarchyNode(name="Arm", kind="product"))
        wrist = await api.create_hierarchy_node(HierarchyNode(name="Wrist", kind="subsystem"))
        await api.add_edge(root.id, wrist.id, EdgeType.CONTAINS, {"quantity": 2})
        actuator_slot = await api.create_hierarchy_node(
            HierarchyNode(name="Actuator", kind="assembly")
        )
        await api.add_edge(wrist.id, actuator_slot.id, EdgeType.CONTAINS, {"quantity": 3})
        bom = await api.add_bom_item(BOMItem(part_number="igus", manufacturer="igus"))
        await api.add_edge(actuator_slot.id, bom.id, EdgeType.INSTANCE_OF)

        lines = await compute_hierarchical_bom(api, root.id)
        assert len(lines) == 1
        assert lines[0].quantity == 6
        assert lines[0].path == ["Arm", "Wrist", "Actuator"]

    async def test_missing_quantity_defaults_to_one(self, api):
        root = await api.create_hierarchy_node(HierarchyNode(name="Arm", kind="product"))
        base = await api.create_hierarchy_node(HierarchyNode(name="Base", kind="subsystem"))
        await api.add_edge(root.id, base.id, EdgeType.CONTAINS)  # no metadata at all
        part = await api.create_work_product(_cad_model("Base plate"))
        await api.add_edge(base.id, part.id, EdgeType.REALIZED_BY)

        lines = await compute_hierarchical_bom(api, root.id)
        assert lines[0].quantity == 1

    async def test_multiple_branches_each_produce_their_own_lines(self, api):
        root = await api.create_hierarchy_node(HierarchyNode(name="Arm", kind="product"))
        base = await api.create_hierarchy_node(HierarchyNode(name="Base", kind="subsystem"))
        shoulder = await api.create_hierarchy_node(HierarchyNode(name="Shoulder", kind="subsystem"))
        await api.add_edge(root.id, base.id, EdgeType.CONTAINS, {"quantity": 1})
        await api.add_edge(root.id, shoulder.id, EdgeType.CONTAINS, {"quantity": 1})
        base_part = await api.create_work_product(_cad_model("Base plate"))
        shoulder_part = await api.create_work_product(_cad_model("Shoulder housing"))
        await api.add_edge(base.id, base_part.id, EdgeType.REALIZED_BY)
        await api.add_edge(shoulder.id, shoulder_part.id, EdgeType.REALIZED_BY)

        lines = await compute_hierarchical_bom(api, root.id)
        assert len(lines) == 2
        descriptions = {line.description for line in lines}
        assert descriptions == {"Base plate", "Shoulder housing"}

    async def test_unknown_root_raises_key_error(self, api):
        with pytest.raises(KeyError):
            await compute_hierarchical_bom(api, uuid4())

    async def test_root_that_is_not_a_hierarchy_node_raises_key_error(self, api):
        wp = await api.create_work_product(_cad_model("Bracket"))
        with pytest.raises(KeyError):
            await compute_hierarchical_bom(api, wp.id)

    async def test_intermediate_node_with_no_leaf_produces_no_line(self, api):
        """A subsystem with no REALIZED_BY/INSTANCE_OF of its own (not yet
        designed) contributes nothing -- not a placeholder line."""
        root = await api.create_hierarchy_node(HierarchyNode(name="Arm", kind="product"))
        empty_subsystem = await api.create_hierarchy_node(
            HierarchyNode(name="Not designed yet", kind="subsystem")
        )
        await api.add_edge(root.id, empty_subsystem.id, EdgeType.CONTAINS, {"quantity": 1})

        lines = await compute_hierarchical_bom(api, root.id)
        assert lines == []
