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


def _electronics(name: str, **power: float) -> WorkProduct:
    return WorkProduct(
        name=name,
        type=WorkProductType.CAD_MODEL,
        domain="electronics",
        file_path="",
        content_hash="deadbeef",
        format="step",
        created_by="test",
        metadata=dict(power),
    )


def _part(name: str, **specs: float) -> BOMItem:
    return BOMItem(part_number=name, manufacturer="ti", specifications=dict(specs))


class TestPowerRollup:
    """Draw and dissipation, tracked apart (FORGE-390).

    They are different budgets checked against different limits: draw
    against supply or battery capacity, dissipation against what the
    enclosure can shed. They are linked by ``dissipation = draw - output``
    rather than being the same number.

    For most electronics the useful output is near zero and the two nearly
    coincide, which is why merging them looks harmless. For a motor, an LED
    or a transmitter it is not: merging gets one budget wrong every time --
    thermal overestimated, or supply underestimated.
    """

    async def test_peak_and_average_are_separate_totals(self, api):
        """Peak answers brown-outs and inrush; average answers battery
        life. One number cannot answer both."""
        root = await api.create_hierarchy_node(HierarchyNode(name="Board", kind="subsystem"))
        wp = await api.create_work_product(
            _electronics("Rail", draw_peak_w=5.0, draw_average_w=1.2, output_w=0.0)
        )
        await api.add_edge(root.id, wp.id, EdgeType.REALIZED_BY)

        result = await compute_hierarchy_rollup(api, root.id)
        assert result.draw_peak_w == 5.0
        assert result.draw_average_w == 1.2

    async def test_dissipation_is_draw_minus_output(self, api):
        """A motor: most of the draw leaves as shaft work, so the heat the
        enclosure has to shed is far less than the supply has to deliver."""
        root = await api.create_hierarchy_node(HierarchyNode(name="Joint", kind="subsystem"))
        wp = await api.create_work_product(
            _electronics("Motor", draw_average_w=20.0, output_w=16.0)
        )
        await api.add_edge(root.id, wp.id, EdgeType.REALIZED_BY)

        result = await compute_hierarchy_rollup(api, root.id)
        assert result.draw_average_w == 20.0
        assert result.output_w == 16.0
        assert result.dissipation_w == 4.0

    async def test_no_output_defaults_dissipation_to_draw_and_says_so(self, api):
        """The right default -- most electronics turn nearly all draw into
        heat -- and wrong for anything that does work, so it is recorded as
        an assumption rather than applied silently (F3)."""
        root = await api.create_hierarchy_node(HierarchyNode(name="MCU", kind="subsystem"))
        wp = await api.create_work_product(_electronics("MCU", draw_average_w=0.4))
        await api.add_edge(root.id, wp.id, EdgeType.REALIZED_BY)

        result = await compute_hierarchy_rollup(api, root.id)
        assert result.dissipation_w == 0.4
        assert len(result.power_assumptions) == 1
        assert result.power_assumptions[0].draw_w == 0.4
        assert "assumed equal to draw" in result.power_assumptions[0].reason

    async def test_a_declared_output_records_no_assumption(self, api):
        root = await api.create_hierarchy_node(HierarchyNode(name="Joint", kind="subsystem"))
        wp = await api.create_work_product(
            _electronics("Motor", draw_average_w=20.0, output_w=16.0)
        )
        await api.add_edge(root.id, wp.id, EdgeType.REALIZED_BY)

        assert (await compute_hierarchy_rollup(api, root.id)).power_assumptions == []

    async def test_a_bare_draw_counts_as_both_peak_and_average(self, api):
        """A caller who gave one number has not distinguished them.
        Treating it as only one would understate the other budget."""
        root = await api.create_hierarchy_node(HierarchyNode(name="Board", kind="subsystem"))
        wp = await api.create_work_product(_electronics("Sensor", draw_w=0.05))
        await api.add_edge(root.id, wp.id, EdgeType.REALIZED_BY)

        result = await compute_hierarchy_rollup(api, root.id)
        assert result.draw_peak_w == 0.05
        assert result.draw_average_w == 0.05

    async def test_a_cots_part_carries_its_own_power(self, api):
        """Read from BOMItem.specifications, which is where component
        selection already writes."""
        root = await api.create_hierarchy_node(HierarchyNode(name="Board", kind="subsystem"))
        part = await api.add_bom_item(_part("TPS62840", draw_average_w=0.3, output_w=0.27))
        await api.add_edge(root.id, part.id, EdgeType.INSTANCE_OF)

        result = await compute_hierarchy_rollup(api, root.id)
        assert result.draw_average_w == 0.3
        assert round(result.dissipation_w, 6) == 0.03

    async def test_quantity_multiplies_power_up_the_tree(self, api):
        """Three identical fingers draw three times as much."""
        root = await api.create_hierarchy_node(HierarchyNode(name="Gripper", kind="assembly"))
        finger = await api.create_hierarchy_node(HierarchyNode(name="Finger", kind="subsystem"))
        wp = await api.create_work_product(_electronics("Servo", draw_average_w=2.0, output_w=1.5))
        await api.add_edge(finger.id, wp.id, EdgeType.REALIZED_BY)
        await api.add_edge(root.id, finger.id, EdgeType.CONTAINS, metadata={"quantity": 3})

        result = await compute_hierarchy_rollup(api, root.id)
        assert result.draw_average_w == 6.0
        assert result.dissipation_w == 1.5

    async def test_output_larger_than_draw_does_not_go_negative(self, api):
        """Bad data, not a heat pump. Negative dissipation would make a
        thermal budget pass by subtracting from its neighbours."""
        root = await api.create_hierarchy_node(HierarchyNode(name="Odd", kind="subsystem"))
        wp = await api.create_work_product(_electronics("Odd", draw_average_w=1.0, output_w=5.0))
        await api.add_edge(root.id, wp.id, EdgeType.REALIZED_BY)

        assert (await compute_hierarchy_rollup(api, root.id)).dissipation_w == 0.0

    async def test_a_non_numeric_power_value_is_skipped_not_coerced(self, api):
        """Same discipline as mass: a rollup silently returning 0 for bad
        data looks identical to genuinely no power yet."""
        root = await api.create_hierarchy_node(HierarchyNode(name="Board", kind="subsystem"))
        wp = await api.create_work_product(
            WorkProduct(
                name="Bad",
                type=WorkProductType.CAD_MODEL,
                domain="electronics",
                file_path="",
                content_hash="deadbeef",
                format="step",
                created_by="test",
                metadata={"draw_average_w": "see datasheet"},
            )
        )
        await api.add_edge(root.id, wp.id, EdgeType.REALIZED_BY)

        result = await compute_hierarchy_rollup(api, root.id)
        assert result.draw_average_w == 0.0
        assert wp.id in result.skipped_node_ids

    async def test_a_node_with_no_power_at_all_reports_zero_without_assuming(self, api):
        """A purely mechanical branch has no power, which is not the same
        as a power figure nobody supplied."""
        root = await api.create_hierarchy_node(HierarchyNode(name="Bracket", kind="subsystem"))
        cad = await api.create_work_product(_cad_model("Bracket", 0.2))
        await api.add_edge(root.id, cad.id, EdgeType.REALIZED_BY)

        result = await compute_hierarchy_rollup(api, root.id)
        assert result.dissipation_w == 0.0
        assert result.power_assumptions == []
