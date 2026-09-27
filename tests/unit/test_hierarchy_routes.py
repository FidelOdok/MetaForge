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
