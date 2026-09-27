"""Product hierarchy tree API (FORGE-261) — /v1/twin/hierarchy."""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import HTTPException

from api_gateway.twin.hierarchy_routes import get_hierarchy_tree, init_twin
from twin_core.api import InMemoryTwinAPI
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
