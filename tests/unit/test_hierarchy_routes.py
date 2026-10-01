"""Product hierarchy tree API (FORGE-261) — /v1/twin/hierarchy."""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import HTTPException

from api_gateway.twin.hierarchy_routes import get_hierarchy_tree, init_twin
from twin_core.api import InMemoryTwinAPI
from twin_core.models.engineering_entity import EngineeringEntity
from twin_core.models.enums import EdgeType, WorkProductType
from twin_core.models.hierarchy_node import HierarchyNode
from twin_core.models.work_product import WorkProduct


class TestGetHierarchyTree:
    async def test_empty_returns_empty_not_error(self) -> None:
        init_twin(InMemoryTwinAPI.create())
        result = await get_hierarchy_tree()
        assert result.nodes == []

    async def test_lists_nodes_with_parent_link_and_rollup(self) -> None:
        twin = InMemoryTwinAPI.create()
        init_twin(twin)
        pid = uuid4()

        product = await twin.create_hierarchy_node(
            HierarchyNode(name="Arm", kind="product", project_id=pid)
        )
        base = await twin.create_hierarchy_node(
            HierarchyNode(name="Base", kind="subsystem", project_id=pid)
        )
        await twin.add_edge(
            product.id, base.id, EdgeType.CONTAINS, {"quantity": 1, "placement": {"z": 0}}
        )
        part = await twin.create_work_product(
            WorkProduct(
                name="Base plate",
                type=WorkProductType.CAD_MODEL,
                domain="mechanical",
                file_path="",
                content_hash="deadbeef",
                format="step",
                created_by="test",
                metadata={"mass_kg": 1.5},
            )
        )
        await twin.add_edge(base.id, part.id, EdgeType.REALIZED_BY)

        result = await get_hierarchy_tree(project_id=str(pid))
        by_id = {n.id: n for n in result.nodes}

        assert len(result.nodes) == 2
        assert by_id[str(product.id)].parentId is None
        assert by_id[str(product.id)].massKg == 1.5  # rolled up from base's own mass

        assert by_id[str(base.id)].parentId == str(product.id)
        assert by_id[str(base.id)].quantity == 1
        assert by_id[str(base.id)].placement == {"z": 0}
        assert by_id[str(base.id)].massKg == 1.5

    async def test_scopes_by_project(self) -> None:
        twin = InMemoryTwinAPI.create()
        init_twin(twin)
        pid_a, pid_b = uuid4(), uuid4()
        await twin.create_hierarchy_node(HierarchyNode(name="A", kind="product", project_id=pid_a))
        await twin.create_hierarchy_node(HierarchyNode(name="B", kind="product", project_id=pid_b))

        scoped = await get_hierarchy_tree(project_id=str(pid_a))
        assert len(scoped.nodes) == 1
        assert scoped.nodes[0].name == "A"

    async def test_root_with_no_parent_edge_has_null_parent_id(self) -> None:
        twin = InMemoryTwinAPI.create()
        init_twin(twin)
        root = await twin.create_hierarchy_node(HierarchyNode(name="Arm", kind="product"))

        result = await get_hierarchy_tree()
        assert result.nodes[0].id == str(root.id)
        assert result.nodes[0].parentId is None
        assert result.nodes[0].quantity is None

    async def test_invalid_project_id_is_400(self) -> None:
        init_twin(InMemoryTwinAPI.create())
        with pytest.raises(HTTPException) as exc:
            await get_hierarchy_tree(project_id="not-a-uuid")
        assert exc.value.status_code == 400


class TestBudgetAllocationWiring:
    """FORGE-264 (gap G-B4): mass/cost budget allocation fields."""

    async def test_over_budget_node_is_flagged(self) -> None:
        twin = InMemoryTwinAPI.create()
        init_twin(twin)
        pid = uuid4()

        base = await twin.create_hierarchy_node(
            HierarchyNode(name="Base", kind="subsystem", project_id=pid)
        )
        part = await twin.create_work_product(
            WorkProduct(
                name="Base plate",
                type=WorkProductType.CAD_MODEL,
                domain="mechanical",
                file_path="",
                content_hash="deadbeef",
                format="step",
                created_by="test",
                metadata={"mass_kg": 5.16},
            )
        )
        await twin.add_edge(base.id, part.id, EdgeType.REALIZED_BY)
        await twin.create_engineering_entity(
            EngineeringEntity(
                entity_type="budget",
                statement="Moving mass budget",
                title="mass_budget",
                project_id=pid,
                metadata={
                    "metric": "mass",
                    "unit": "kg",
                    "system_total": 4.5,
                    "allocations": [{"target": str(base.id), "amount": 4.5}],
                },
            )
        )

        result = await get_hierarchy_tree(project_id=str(pid))
        node = next(n for n in result.nodes if n.id == str(base.id))
        assert node.massBudgetKg == 4.5
        assert node.massOverBudget is True

    async def test_allocation_owner_and_discipline_surfaced(self) -> None:
        # FORGE-313
        twin = InMemoryTwinAPI.create()
        init_twin(twin)
        pid = uuid4()

        base = await twin.create_hierarchy_node(
            HierarchyNode(name="Base", kind="subsystem", project_id=pid)
        )
        await twin.create_engineering_entity(
            EngineeringEntity(
                entity_type="budget",
                statement="Moving mass budget",
                title="mass_budget",
                project_id=pid,
                metadata={
                    "metric": "mass",
                    "unit": "kg",
                    "system_total": 4.5,
                    "allocations": [
                        {
                            "target": str(base.id),
                            "amount": 4.5,
                            "owner": "alice",
                            "discipline": "mechanical",
                        }
                    ],
                },
            )
        )

        result = await get_hierarchy_tree(project_id=str(pid))
        node = next(n for n in result.nodes if n.id == str(base.id))
        assert node.massBudgetOwner == "alice"
        assert node.massBudgetDiscipline == "mechanical"

    async def test_under_budget_node_is_not_flagged(self) -> None:
        twin = InMemoryTwinAPI.create()
        init_twin(twin)
        pid = uuid4()

        base = await twin.create_hierarchy_node(
            HierarchyNode(name="Base", kind="subsystem", project_id=pid)
        )
        part = await twin.create_work_product(
            WorkProduct(
                name="Base plate",
                type=WorkProductType.CAD_MODEL,
                domain="mechanical",
                file_path="",
                content_hash="deadbeef",
                format="step",
                created_by="test",
                metadata={"mass_kg": 1.0},
            )
        )
        await twin.add_edge(base.id, part.id, EdgeType.REALIZED_BY)
        await twin.create_engineering_entity(
            EngineeringEntity(
                entity_type="budget",
                statement="Moving mass budget",
                title="mass_budget",
                project_id=pid,
                metadata={
                    "metric": "mass",
                    "unit": "kg",
                    "system_total": 4.5,
                    "allocations": [{"target": str(base.id), "amount": 4.5}],
                },
            )
        )

        result = await get_hierarchy_tree(project_id=str(pid))
        node = next(n for n in result.nodes if n.id == str(base.id))
        assert node.massOverBudget is False

    async def test_unallocated_node_has_no_budget_fields(self) -> None:
        twin = InMemoryTwinAPI.create()
        init_twin(twin)
        pid = uuid4()
        node = await twin.create_hierarchy_node(
            HierarchyNode(name="Untracked", kind="assembly", project_id=pid)
        )

        result = await get_hierarchy_tree(project_id=str(pid))
        out = next(n for n in result.nodes if n.id == str(node.id))
        assert out.massBudgetKg is None
        assert out.massOverBudget is None

    async def test_malformed_budget_entity_does_not_break_the_list(self) -> None:
        twin = InMemoryTwinAPI.create()
        init_twin(twin)
        pid = uuid4()
        node = await twin.create_hierarchy_node(
            HierarchyNode(name="Base", kind="subsystem", project_id=pid)
        )
        # Missing required 'system_total' -- budget_from_entity raises ValueError.
        await twin.create_engineering_entity(
            EngineeringEntity(
                entity_type="budget",
                statement="Broken budget",
                project_id=pid,
                metadata={"metric": "mass", "unit": "kg"},
            )
        )

        result = await get_hierarchy_tree(project_id=str(pid))
        assert len(result.nodes) == 1
        assert result.nodes[0].id == str(node.id)

    async def test_unscoped_listing_skips_budget_lookup(self) -> None:
        """No project_id means no single project's budgets apply -- must
        not guess, and must not error."""
        twin = InMemoryTwinAPI.create()
        init_twin(twin)
        await twin.create_hierarchy_node(HierarchyNode(name="Arm", kind="product"))

        result = await get_hierarchy_tree()
        assert result.nodes[0].massBudgetKg is None


class TestInterfacesWiring:
    """FORGE-313: interfaces per node, resolved from a SYSTEM_ARCHITECTURE
    work product's structured `interfaces` metadata."""

    async def test_interface_surfaced_on_both_named_components(self) -> None:
        twin = InMemoryTwinAPI.create()
        init_twin(twin)
        pid = uuid4()

        upper_arm = await twin.create_hierarchy_node(
            HierarchyNode(name="upper_arm", kind="subsystem", project_id=pid)
        )
        shoulder = await twin.create_hierarchy_node(
            HierarchyNode(name="shoulder", kind="subsystem", project_id=pid)
        )
        await twin.create_work_product(
            WorkProduct(
                name="Arm architecture",
                type=WorkProductType.SYSTEM_ARCHITECTURE,
                domain="systems",
                file_path="",
                content_hash="deadbeef",
                format="md",
                created_by="test",
                project_id=pid,
                metadata={
                    "interfaces": [
                        {
                            "from": "upper_arm",
                            "to": "shoulder",
                            "interface_type": "mechanical",
                            "description": "joint",
                            "quantities": [
                                {"metric": "tip_deflection", "unit": "mm", "limit": 0.5, "op": "<="}
                            ],
                        }
                    ]
                },
            )
        )

        result = await get_hierarchy_tree(project_id=str(pid))
        by_id = {n.id: n for n in result.nodes}

        ua_ifaces = by_id[str(upper_arm.id)].interfaces
        assert len(ua_ifaces) == 1
        assert ua_ifaces[0].otherComponent == "shoulder"
        assert ua_ifaces[0].quantities[0].metric == "tip_deflection"

        sh_ifaces = by_id[str(shoulder.id)].interfaces
        assert len(sh_ifaces) == 1
        assert sh_ifaces[0].otherComponent == "upper_arm"

    async def test_no_system_architecture_means_empty_interfaces(self) -> None:
        twin = InMemoryTwinAPI.create()
        init_twin(twin)
        pid = uuid4()
        node = await twin.create_hierarchy_node(
            HierarchyNode(name="upper_arm", kind="subsystem", project_id=pid)
        )
        result = await get_hierarchy_tree(project_id=str(pid))
        assert result.nodes[0].id == str(node.id)
        assert result.nodes[0].interfaces == []


class TestGeometryFields:
    """FORGE-266 (gap G-C2): realizedByWorkProductId/instanceOfBomItemId --
    the dashboard's cue for "placeholder" (both None) vs "realized"."""

    async def test_placeholder_node_has_null_geometry_fields(self) -> None:
        twin = InMemoryTwinAPI.create()
        init_twin(twin)
        node = await twin.create_hierarchy_node(HierarchyNode(name="Bracket slot", kind="assembly"))

        result = await get_hierarchy_tree()
        assert result.nodes[0].id == str(node.id)
        assert result.nodes[0].realizedByWorkProductId is None
        assert result.nodes[0].instanceOfBomItemId is None

    async def test_realized_by_edge_is_surfaced(self) -> None:
        twin = InMemoryTwinAPI.create()
        init_twin(twin)
        node = await twin.create_hierarchy_node(HierarchyNode(name="Bracket slot", kind="assembly"))
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
        await twin.add_edge(node.id, cad.id, EdgeType.REALIZED_BY)

        result = await get_hierarchy_tree()
        assert result.nodes[0].realizedByWorkProductId == str(cad.id)
        assert result.nodes[0].instanceOfBomItemId is None

    async def test_instance_of_edge_is_surfaced(self) -> None:
        from twin_core.models.bom_item import BOMItem

        twin = InMemoryTwinAPI.create()
        init_twin(twin)
        node = await twin.create_hierarchy_node(
            HierarchyNode(name="Actuator slot", kind="assembly")
        )
        bom = await twin.add_bom_item(BOMItem(part_number="DS3218MG", manufacturer="Miuzei"))
        await twin.add_edge(node.id, bom.id, EdgeType.INSTANCE_OF)

        result = await get_hierarchy_tree()
        assert result.nodes[0].instanceOfBomItemId == str(bom.id)
        assert result.nodes[0].realizedByWorkProductId is None


class TestPowerFields:
    """FORGE-275 (gap G-E2): drawPeakW/drawAverageW/outputW/dissipationW --
    the hierarchy rollup has computed these since FORGE-390; this just
    surfaces them to the dashboard tree, turning it into a power-tree view."""

    async def test_node_with_no_power_data_reports_zero(self) -> None:
        twin = InMemoryTwinAPI.create()
        init_twin(twin)
        node = await twin.create_hierarchy_node(HierarchyNode(name="Bracket slot", kind="assembly"))

        result = await get_hierarchy_tree()
        assert result.nodes[0].id == str(node.id)
        assert result.nodes[0].drawPeakW == 0.0
        assert result.nodes[0].drawAverageW == 0.0
        assert result.nodes[0].outputW == 0.0
        assert result.nodes[0].dissipationW == 0.0

    async def test_realized_by_power_data_rolls_up(self) -> None:
        twin = InMemoryTwinAPI.create()
        init_twin(twin)
        node = await twin.create_hierarchy_node(
            HierarchyNode(name="Actuator slot", kind="assembly")
        )
        cad = await twin.create_work_product(
            WorkProduct(
                name="Actuator",
                type=WorkProductType.CAD_MODEL,
                domain="mechanical",
                file_path="",
                content_hash="deadbeef",
                format="step",
                created_by="test",
                metadata={"draw_peak_w": 20.0, "draw_average_w": 10.0, "output_w": 6.0},
            )
        )
        await twin.add_edge(node.id, cad.id, EdgeType.REALIZED_BY)

        result = await get_hierarchy_tree()
        assert result.nodes[0].drawPeakW == 20.0
        assert result.nodes[0].drawAverageW == 10.0
        assert result.nodes[0].outputW == 6.0
        assert result.nodes[0].dissipationW == 4.0  # 10 (average) - 6 (output)

    async def test_instance_of_bom_item_power_data_rolls_up(self) -> None:
        from twin_core.models.bom_item import BOMItem

        twin = InMemoryTwinAPI.create()
        init_twin(twin)
        node = await twin.create_hierarchy_node(
            HierarchyNode(name="Actuator slot", kind="assembly")
        )
        bom = await twin.add_bom_item(
            BOMItem(
                part_number="AK80-8 KV60",
                manufacturer="CubeMars",
                specifications={"draw_average_w": 331.2},
            )
        )
        await twin.add_edge(node.id, bom.id, EdgeType.INSTANCE_OF)

        result = await get_hierarchy_tree()
        assert result.nodes[0].drawAverageW == 331.2
        # No output_w declared -- dissipation is assumed equal to draw (FORGE-390).
        assert result.nodes[0].dissipationW == 331.2


class TestRealizeHierarchyNodeRoute:
    @pytest.fixture(autouse=True)
    def _wire(self):
        from api_gateway.twin.hierarchy_recorder import make_hierarchy_geometry_linker
        from api_gateway.twin.hierarchy_routes import init_hierarchy_geometry_linker

        self.twin = InMemoryTwinAPI.create()
        init_twin(self.twin)
        init_hierarchy_geometry_linker(make_hierarchy_geometry_linker(self.twin))
        yield
        init_hierarchy_geometry_linker(None)

    async def test_sets_realized_by_via_the_route(self) -> None:
        from api_gateway.twin.hierarchy_routes import (
            RealizeHierarchyNodeRequest,
            realize_hierarchy_node,
        )

        node = await self.twin.create_hierarchy_node(HierarchyNode(name="x", kind="assembly"))
        cad = await self.twin.create_work_product(
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

        result = await realize_hierarchy_node(
            str(node.id), RealizeHierarchyNodeRequest(workProductId=str(cad.id))
        )
        assert result.realizedByWorkProductId == str(cad.id)

        tree = await get_hierarchy_tree()
        assert tree.nodes[0].realizedByWorkProductId == str(cad.id)

    async def test_unavailable_linker_503s(self) -> None:
        from api_gateway.twin.hierarchy_routes import (
            RealizeHierarchyNodeRequest,
            init_hierarchy_geometry_linker,
            realize_hierarchy_node,
        )

        init_hierarchy_geometry_linker(None)
        with pytest.raises(HTTPException) as exc:
            await realize_hierarchy_node(str(uuid4()), RealizeHierarchyNodeRequest())
        assert exc.value.status_code == 503

    async def test_invalid_target_is_400(self) -> None:
        from api_gateway.twin.hierarchy_routes import (
            RealizeHierarchyNodeRequest,
            realize_hierarchy_node,
        )

        with pytest.raises(HTTPException) as exc:
            await realize_hierarchy_node(
                str(uuid4()), RealizeHierarchyNodeRequest(workProductId=str(uuid4()))
            )
        assert exc.value.status_code == 400
