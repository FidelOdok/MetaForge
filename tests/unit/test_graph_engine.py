"""Unit tests for the InMemoryGraphEngine."""

from uuid import uuid4

import pytest

from twin_core.graph_engine import InMemoryGraphEngine
from twin_core.models import (
    Artifact,
    ArtifactType,
    Component,
    Constraint,
    ConstraintSeverity,
    DependsOnEdge,
    EdgeBase,
    EdgeType,
    NodeType,
)


@pytest.fixture
def engine():
    return InMemoryGraphEngine()


def _make_artifact(name: str = "test", domain: str = "mechanical") -> Artifact:
    return Artifact(
        name=name,
        type=ArtifactType.CAD_MODEL,
        domain=domain,
        file_path=f"models/{name}.step",
        content_hash="hash123",
        format="step",
        created_by="human",
    )


def _make_constraint(name: str = "test_constraint", domain: str = "mechanical") -> Constraint:
    return Constraint(
        name=name,
        expression="True",
        severity=ConstraintSeverity.WARNING,
        domain=domain,
        source="user",
    )


# --- Node CRUD ---


class TestNodeCRUD:
    async def test_add_and_get_node(self, engine):
        a = _make_artifact()
        result = await engine.add_node(a)
        assert result.id == a.id

        fetched = await engine.get_node(a.id)
        assert fetched is not None
        assert fetched.name == "test"

    async def test_add_duplicate_id_raises(self, engine):
        a = _make_artifact()
        await engine.add_node(a)
        with pytest.raises(ValueError, match="already exists"):
            await engine.add_node(a)

    async def test_get_nonexistent_returns_none(self, engine):
        assert await engine.get_node(uuid4()) is None

    async def test_update_node(self, engine):
        a = _make_artifact()
        await engine.add_node(a)

        updated = await engine.update_node(a.id, {"name": "updated_name"})
        assert updated.name == "updated_name"

        fetched = await engine.get_node(a.id)
        assert fetched.name == "updated_name"

    async def test_update_bumps_updated_at(self, engine):
        a = _make_artifact()
        await engine.add_node(a)
        original_updated = a.updated_at

        updated = await engine.update_node(a.id, {"name": "new"})
        assert updated.updated_at >= original_updated

    async def test_update_nonexistent_raises(self, engine):
        with pytest.raises(KeyError):
            await engine.update_node(uuid4(), {"name": "x"})

    async def test_delete_node(self, engine):
        a = _make_artifact()
        await engine.add_node(a)
        assert await engine.delete_node(a.id) is True
        assert await engine.get_node(a.id) is None

    async def test_delete_nonexistent_returns_false(self, engine):
        assert await engine.delete_node(uuid4()) is False

    async def test_delete_removes_connected_edges(self, engine):
        a = _make_artifact("a")
        b = _make_artifact("b")
        await engine.add_node(a)
        await engine.add_node(b)

        edge = EdgeBase(source_id=a.id, target_id=b.id, edge_type=EdgeType.DEPENDS_ON)
        await engine.add_edge(edge)

        await engine.delete_node(a.id)
        # Edge should be gone from b's incoming
        edges = await engine.get_edges(b.id, direction="incoming")
        assert len(edges) == 0

    async def test_add_different_node_types(self, engine):
        a = _make_artifact()
        c = _make_constraint()
        comp = Component(part_number="STM32F407", manufacturer="ST")

        await engine.add_node(a)
        await engine.add_node(c)
        await engine.add_node(comp)

        assert (await engine.get_node(a.id)).node_type == NodeType.ARTIFACT
        assert (await engine.get_node(c.id)).node_type == NodeType.CONSTRAINT
        assert (await engine.get_node(comp.id)).node_type == NodeType.COMPONENT


# --- list_nodes ---


class TestListNodes:
    async def test_list_all(self, engine):
        a = _make_artifact("a")
        b = _make_artifact("b")
        await engine.add_node(a)
        await engine.add_node(b)
        assert len(await engine.list_nodes()) == 2

    async def test_filter_by_node_type(self, engine):
        a = _make_artifact()
        c = _make_constraint()
        await engine.add_node(a)
        await engine.add_node(c)

        artifacts = await engine.list_nodes(node_type=NodeType.ARTIFACT)
        assert len(artifacts) == 1
        assert artifacts[0].id == a.id

    async def test_filter_by_domain(self, engine):
        a = _make_artifact("a", domain="mechanical")
        b = _make_artifact("b", domain="electronics")
        await engine.add_node(a)
        await engine.add_node(b)

        results = await engine.list_nodes(filters={"domain": "electronics"})
        assert len(results) == 1
        assert results[0].id == b.id

    async def test_filter_by_type_and_domain(self, engine):
        a = _make_artifact("a", domain="mechanical")
        c = _make_constraint("c", domain="mechanical")
        await engine.add_node(a)
        await engine.add_node(c)

        results = await engine.list_nodes(
            node_type=NodeType.ARTIFACT, filters={"domain": "mechanical"}
        )
        assert len(results) == 1
        assert results[0].id == a.id

    async def test_empty_graph(self, engine):
        assert await engine.list_nodes() == []


# --- Edge CRUD ---


class TestEdgeCRUD:
    async def test_add_and_get_edge(self, engine):
        a = _make_artifact("a")
        b = _make_artifact("b")
        await engine.add_node(a)
        await engine.add_node(b)

        edge = EdgeBase(source_id=a.id, target_id=b.id, edge_type=EdgeType.DEPENDS_ON)
        result = await engine.add_edge(edge)
        assert result.source_id == a.id

        outgoing = await engine.get_edges(a.id, direction="outgoing")
        assert len(outgoing) == 1
        assert outgoing[0].target_id == b.id

    async def test_get_incoming_edges(self, engine):
        a = _make_artifact("a")
        b = _make_artifact("b")
        await engine.add_node(a)
        await engine.add_node(b)

        edge = EdgeBase(source_id=a.id, target_id=b.id, edge_type=EdgeType.DEPENDS_ON)
        await engine.add_edge(edge)

        incoming = await engine.get_edges(b.id, direction="incoming")
        assert len(incoming) == 1
        assert incoming[0].source_id == a.id

    async def test_get_both_directions(self, engine):
        a = _make_artifact("a")
        b = _make_artifact("b")
        c = _make_artifact("c")
        await engine.add_node(a)
        await engine.add_node(b)
        await engine.add_node(c)

        await engine.add_edge(
            EdgeBase(source_id=a.id, target_id=b.id, edge_type=EdgeType.DEPENDS_ON)
        )
        await engine.add_edge(
            EdgeBase(source_id=c.id, target_id=b.id, edge_type=EdgeType.VALIDATES)
        )

        both = await engine.get_edges(b.id, direction="both")
        assert len(both) == 2

    async def test_filter_by_edge_type(self, engine):
        a = _make_artifact("a")
        b = _make_artifact("b")
        await engine.add_node(a)
        await engine.add_node(b)

        await engine.add_edge(
            EdgeBase(source_id=a.id, target_id=b.id, edge_type=EdgeType.DEPENDS_ON)
        )
        await engine.add_edge(
            EdgeBase(source_id=a.id, target_id=b.id, edge_type=EdgeType.VALIDATES)
        )

        deps_only = await engine.get_edges(
            a.id, direction="outgoing", edge_type=EdgeType.DEPENDS_ON
        )
        assert len(deps_only) == 1

    async def test_add_edge_missing_source(self, engine):
        b = _make_artifact("b")
        await engine.add_node(b)
        with pytest.raises(ValueError, match="Source node"):
            await engine.add_edge(
                EdgeBase(source_id=uuid4(), target_id=b.id, edge_type=EdgeType.DEPENDS_ON)
            )

    async def test_add_edge_missing_target(self, engine):
        a = _make_artifact("a")
        await engine.add_node(a)
        with pytest.raises(ValueError, match="Target node"):
            await engine.add_edge(
                EdgeBase(source_id=a.id, target_id=uuid4(), edge_type=EdgeType.DEPENDS_ON)
            )

    async def test_remove_edge(self, engine):
        a = _make_artifact("a")
        b = _make_artifact("b")
        await engine.add_node(a)
        await engine.add_node(b)

        await engine.add_edge(
            EdgeBase(source_id=a.id, target_id=b.id, edge_type=EdgeType.DEPENDS_ON)
        )
        assert await engine.remove_edge(a.id, b.id, EdgeType.DEPENDS_ON) is True
        assert await engine.get_edges(a.id, direction="outgoing") == []

    async def test_remove_nonexistent_edge(self, engine):
        assert await engine.remove_edge(uuid4(), uuid4(), EdgeType.DEPENDS_ON) is False

    async def test_typed_edge(self, engine):
        a = _make_artifact("a")
        b = _make_artifact("b")
        await engine.add_node(a)
        await engine.add_node(b)

        edge = DependsOnEdge(
            source_id=a.id,
            target_id=b.id,
            dependency_type="soft",
            description="Optional ref",
        )
        result = await engine.add_edge(edge)
        assert result.edge_type == EdgeType.DEPENDS_ON

        edges = await engine.get_edges(a.id)
        assert len(edges) == 1
        assert isinstance(edges[0], DependsOnEdge)
        assert edges[0].dependency_type == "soft"


# --- Traversal ---


class TestGetNeighbors:
    async def test_outgoing_neighbors(self, engine):
        a = _make_artifact("a")
        b = _make_artifact("b")
        c = _make_artifact("c")
        await engine.add_node(a)
        await engine.add_node(b)
        await engine.add_node(c)

        await engine.add_edge(
            EdgeBase(source_id=a.id, target_id=b.id, edge_type=EdgeType.DEPENDS_ON)
        )
        await engine.add_edge(EdgeBase(source_id=a.id, target_id=c.id, edge_type=EdgeType.CONTAINS))

        neighbors = await engine.get_neighbors(a.id)
        assert len(neighbors) == 2
        neighbor_ids = {n.id for n in neighbors}
        assert b.id in neighbor_ids
        assert c.id in neighbor_ids

    async def test_incoming_neighbors(self, engine):
        a = _make_artifact("a")
        b = _make_artifact("b")
        await engine.add_node(a)
        await engine.add_node(b)

        await engine.add_edge(
            EdgeBase(source_id=a.id, target_id=b.id, edge_type=EdgeType.DEPENDS_ON)
        )

        neighbors = await engine.get_neighbors(b.id, direction="incoming")
        assert len(neighbors) == 1
        assert neighbors[0].id == a.id

    async def test_filter_by_edge_type(self, engine):
        a = _make_artifact("a")
        b = _make_artifact("b")
        c = _make_artifact("c")
        await engine.add_node(a)
        await engine.add_node(b)
        await engine.add_node(c)

        await engine.add_edge(
            EdgeBase(source_id=a.id, target_id=b.id, edge_type=EdgeType.DEPENDS_ON)
        )
        await engine.add_edge(EdgeBase(source_id=a.id, target_id=c.id, edge_type=EdgeType.CONTAINS))

        neighbors = await engine.get_neighbors(a.id, edge_type=EdgeType.DEPENDS_ON)
        assert len(neighbors) == 1
        assert neighbors[0].id == b.id


class TestGetSubgraph:
    async def test_single_node(self, engine):
        a = _make_artifact("a")
        await engine.add_node(a)

        sg = await engine.get_subgraph(a.id, depth=2)
        assert len(sg.nodes) == 1
        assert len(sg.edges) == 0
        assert sg.root_id == a.id

    async def test_depth_limiting(self, engine):
        # a -> b -> c -> d
        a, b, c, d = (
            _make_artifact("a"),
            _make_artifact("b"),
            _make_artifact("c"),
            _make_artifact("d"),
        )
        for node in [a, b, c, d]:
            await engine.add_node(node)

        await engine.add_edge(
            EdgeBase(source_id=a.id, target_id=b.id, edge_type=EdgeType.DEPENDS_ON)
        )
        await engine.add_edge(
            EdgeBase(source_id=b.id, target_id=c.id, edge_type=EdgeType.DEPENDS_ON)
        )
        await engine.add_edge(
            EdgeBase(source_id=c.id, target_id=d.id, edge_type=EdgeType.DEPENDS_ON)
        )

        sg = await engine.get_subgraph(a.id, depth=2)
        node_ids = {n.id for n in sg.nodes}
        assert a.id in node_ids
        assert b.id in node_ids
        assert c.id in node_ids
        assert d.id not in node_ids  # depth=2 means 2 hops: a->b->c

    async def test_edge_type_filter(self, engine):
        a = _make_artifact("a")
        b = _make_artifact("b")
        c = _make_artifact("c")
        await engine.add_node(a)
        await engine.add_node(b)
        await engine.add_node(c)

        await engine.add_edge(
            EdgeBase(source_id=a.id, target_id=b.id, edge_type=EdgeType.DEPENDS_ON)
        )
        await engine.add_edge(EdgeBase(source_id=a.id, target_id=c.id, edge_type=EdgeType.CONTAINS))

        sg = await engine.get_subgraph(a.id, depth=1, edge_types=[EdgeType.DEPENDS_ON])
        node_ids = {n.id for n in sg.nodes}
        assert b.id in node_ids
        assert c.id not in node_ids

    async def test_nonexistent_root_raises(self, engine):
        with pytest.raises(KeyError):
            await engine.get_subgraph(uuid4(), depth=2)


class TestTraverse:
    async def test_linear_chain(self, engine):
        # a -> b -> c
        a, b, c = _make_artifact("a"), _make_artifact("b"), _make_artifact("c")
        for node in [a, b, c]:
            await engine.add_node(node)

        await engine.add_edge(
            EdgeBase(source_id=a.id, target_id=b.id, edge_type=EdgeType.DEPENDS_ON)
        )
        await engine.add_edge(
            EdgeBase(source_id=b.id, target_id=c.id, edge_type=EdgeType.DEPENDS_ON)
        )

        paths = await engine.traverse(a.id, [EdgeType.DEPENDS_ON], max_depth=5)
        assert len(paths) == 1
        assert paths[0] == [a.id, b.id, c.id]

    async def test_branching_paths(self, engine):
        # a -> b, a -> c
        a, b, c = _make_artifact("a"), _make_artifact("b"), _make_artifact("c")
        for node in [a, b, c]:
            await engine.add_node(node)

        await engine.add_edge(
            EdgeBase(source_id=a.id, target_id=b.id, edge_type=EdgeType.DEPENDS_ON)
        )
        await engine.add_edge(
            EdgeBase(source_id=a.id, target_id=c.id, edge_type=EdgeType.DEPENDS_ON)
        )

        paths = await engine.traverse(a.id, [EdgeType.DEPENDS_ON], max_depth=5)
        assert len(paths) == 2
        path_ends = {p[-1] for p in paths}
        assert b.id in path_ends
        assert c.id in path_ends

    async def test_max_depth_cutoff(self, engine):
        # a -> b -> c -> d, max_depth=1
        a, b, c, d = (
            _make_artifact("a"),
            _make_artifact("b"),
            _make_artifact("c"),
            _make_artifact("d"),
        )
        for node in [a, b, c, d]:
            await engine.add_node(node)

        await engine.add_edge(
            EdgeBase(source_id=a.id, target_id=b.id, edge_type=EdgeType.DEPENDS_ON)
        )
        await engine.add_edge(
            EdgeBase(source_id=b.id, target_id=c.id, edge_type=EdgeType.DEPENDS_ON)
        )
        await engine.add_edge(
            EdgeBase(source_id=c.id, target_id=d.id, edge_type=EdgeType.DEPENDS_ON)
        )

        paths = await engine.traverse(a.id, [EdgeType.DEPENDS_ON], max_depth=1)
        assert len(paths) == 1
        assert paths[0] == [a.id, b.id]

    async def test_no_matching_edges(self, engine):
        a = _make_artifact("a")
        b = _make_artifact("b")
        await engine.add_node(a)
        await engine.add_node(b)

        await engine.add_edge(EdgeBase(source_id=a.id, target_id=b.id, edge_type=EdgeType.CONTAINS))

        # Traverse looking for DEPENDS_ON, but only CONTAINS exists
        paths = await engine.traverse(a.id, [EdgeType.DEPENDS_ON], max_depth=5)
        assert len(paths) == 0

    async def test_cycle_avoidance(self, engine):
        # a -> b -> a (cycle)
        a, b = _make_artifact("a"), _make_artifact("b")
        await engine.add_node(a)
        await engine.add_node(b)

        await engine.add_edge(
            EdgeBase(source_id=a.id, target_id=b.id, edge_type=EdgeType.DEPENDS_ON)
        )
        await engine.add_edge(
            EdgeBase(source_id=b.id, target_id=a.id, edge_type=EdgeType.DEPENDS_ON)
        )

        paths = await engine.traverse(a.id, [EdgeType.DEPENDS_ON], max_depth=10)
        # Should terminate without infinite loop
        assert len(paths) >= 1
        for path in paths:
            # No repeated nodes in any path
            assert len(path) == len(set(path))

    async def test_nonexistent_root_raises(self, engine):
        with pytest.raises(KeyError):
            await engine.traverse(uuid4(), [EdgeType.DEPENDS_ON])
