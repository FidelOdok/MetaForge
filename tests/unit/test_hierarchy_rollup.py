"""compute_hierarchy_rollup (FORGE-260, gap G-B1) -- per-branch mass/cost
totals over the product hierarchy tree.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from twin_core.api import InMemoryTwinAPI
from twin_core.consistency.hierarchy_rollup import compute_hierarchy_rollup
from twin_core.models.bom_item import BOMItem
from twin_core.models.enums import EdgeType, WorkProductType
from twin_core.models.hierarchy_node import HierarchyNode
from twin_core.models.work_product import WorkProduct


@pytest.fixture
def api():
    return InMemoryTwinAPI.create()


def _cad_model(name: str, mass_kg: float) -> WorkProduct:
    return WorkProduct(
        name=name,
        type=WorkProductType.CAD_MODEL,
        domain="mechanical",
        file_path="",
        content_hash="deadbeef",
        format="step",
        created_by="test",
        metadata={"mass_kg": mass_kg},
    )


def _bom_item(name: str, unit_cost: float) -> BOMItem:
    return BOMItem(part_number=name, manufacturer="igus", unit_cost=unit_cost)


class TestComputeHierarchyRollup:
    async def test_leaf_with_no_links_is_zero(self, api):
        root = await api.create_hierarchy_node(HierarchyNode(name="Arm", kind="product"))
        result = await compute_hierarchy_rollup(api, root.id)
        assert result.mass_kg == 0.0
        assert result.cost == 0.0
        assert result.node_count == 1
        assert result.skipped_node_ids == []

    async def test_single_realized_by_mass(self, api):
        """Matches the ticket's own acceptance yardstick: mass rollup
        matches the sum of part masses."""
        root = await api.create_hierarchy_node(HierarchyNode(name="Base", kind="subsystem"))
        cad = await api.create_work_product(_cad_model("Base plate", 1.5))
        await api.add_edge(root.id, cad.id, EdgeType.REALIZED_BY)

        result = await compute_hierarchy_rollup(api, root.id)
        assert result.mass_kg == 1.5

    async def test_nested_tree_sums_across_the_whole_branch(self, api):
        """Product -> {Base, Shoulder} -> each REALIZED_BY a part -- mirrors
        the arm yardstick's own shape (subsystems each made of parts)."""
        product = await api.create_hierarchy_node(HierarchyNode(name="Arm", kind="product"))
        base = await api.create_hierarchy_node(HierarchyNode(name="Base", kind="subsystem"))
        shoulder = await api.create_hierarchy_node(HierarchyNode(name="Shoulder", kind="subsystem"))
        await api.add_edge(product.id, base.id, EdgeType.CONTAINS, {"quantity": 1})
        await api.add_edge(product.id, shoulder.id, EdgeType.CONTAINS, {"quantity": 1})

        base_part = await api.create_work_product(_cad_model("Base plate", 1.2))
        shoulder_part = await api.create_work_product(_cad_model("Shoulder housing", 0.8))
        await api.add_edge(base.id, base_part.id, EdgeType.REALIZED_BY)
        await api.add_edge(shoulder.id, shoulder_part.id, EdgeType.REALIZED_BY)

        result = await compute_hierarchy_rollup(api, product.id)
        assert result.mass_kg == pytest.approx(2.0)
        assert result.node_count == 3

    async def test_contains_quantity_multiplies_the_whole_child_subtotal(self, api):
        """4 identical standoffs, each 0.05 kg, must roll up to 0.2 kg --
        not just count the CONTAINS edge once."""
        parent = await api.create_hierarchy_node(HierarchyNode(name="Base", kind="subsystem"))
        standoff = await api.create_hierarchy_node(HierarchyNode(name="Standoff", kind="assembly"))
        await api.add_edge(parent.id, standoff.id, EdgeType.CONTAINS, {"quantity": 4})
        part = await api.create_work_product(_cad_model("Standoff M3x10", 0.05))
        await api.add_edge(standoff.id, part.id, EdgeType.REALIZED_BY)

        result = await compute_hierarchy_rollup(api, parent.id)
        assert result.mass_kg == pytest.approx(0.2)

    async def test_missing_quantity_defaults_to_one(self, api):
        parent = await api.create_hierarchy_node(HierarchyNode(name="Base", kind="subsystem"))
        child = await api.create_hierarchy_node(HierarchyNode(name="Bracket", kind="assembly"))
        await api.add_edge(parent.id, child.id, EdgeType.CONTAINS)  # no metadata at all
        part = await api.create_work_product(_cad_model("Bracket", 0.3))
        await api.add_edge(child.id, part.id, EdgeType.REALIZED_BY)

        result = await compute_hierarchy_rollup(api, parent.id)
        assert result.mass_kg == pytest.approx(0.3)

    async def test_instance_of_bom_item_rolls_up_cost(self, api):
        """3x identical igus actuators (the ticket's own example) -- one
        canonical BOMItem, instanced at a single hierarchy position with
        quantity=3 on the CONTAINS edge from its parent."""
        parent = await api.create_hierarchy_node(HierarchyNode(name="Wrist", kind="subsystem"))
        actuator_slot = await api.create_hierarchy_node(
            HierarchyNode(name="Actuator", kind="assembly")
        )
        await api.add_edge(parent.id, actuator_slot.id, EdgeType.CONTAINS, {"quantity": 3})
        actuator = await api.add_bom_item(_bom_item("igus RL-SE-80-50", 42.0))
        await api.add_edge(actuator_slot.id, actuator.id, EdgeType.INSTANCE_OF)

        result = await compute_hierarchy_rollup(api, parent.id)
        assert result.cost == pytest.approx(126.0)
        assert result.mass_kg == 0.0  # BOMItem carries no canonical mass field

    async def test_non_numeric_mass_is_skipped_not_coerced(self, api):
        root = await api.create_hierarchy_node(HierarchyNode(name="Base", kind="subsystem"))
        cad = await api.create_work_product(_cad_model("Base plate", 1.0))
        cad.metadata["mass_kg"] = "unknown"  # a bad value, not absent
        await api.add_edge(root.id, cad.id, EdgeType.REALIZED_BY)

        result = await compute_hierarchy_rollup(api, root.id)
        assert result.mass_kg == 0.0
        assert cad.id in result.skipped_node_ids

    async def test_unknown_root_raises_key_error(self, api):
        with pytest.raises(KeyError):
            await compute_hierarchy_rollup(api, uuid4())

    async def test_root_that_is_not_a_hierarchy_node_raises_key_error(self, api):
        wp = await api.create_work_product(_cad_model("Bracket", 1.0))
        with pytest.raises(KeyError):
            await compute_hierarchy_rollup(api, wp.id)
