"""Unit tests for Neo4jGraphEngine with mocked Neo4j async driver."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from twin_core.models.artifact import Artifact
from twin_core.models.base import EdgeBase, NodeBase
from twin_core.models.enums import ArtifactType, EdgeType, NodeType
from twin_core.neo4j_graph_engine import (
    Neo4jConnectionError,
    Neo4jGraphEngine,
    Neo4jQueryError,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _make_mock_record(node: NodeBase) -> MagicMock:
    """Create a mock Neo4j record that returns node properties."""
    props = Neo4jGraphEngine._node_to_props(node)
    record = MagicMock()
    record.__getitem__ = MagicMock(side_effect=lambda key: props if key == "n" else None)
    record.data = MagicMock(return_value={"n": props})
    return record


def _make_mock_edge_record(edge: EdgeBase) -> MagicMock:
    """Create a mock Neo4j record that returns edge properties."""
    props = Neo4jGraphEngine._edge_to_props(edge)
    record = MagicMock()
    record.__getitem__ = MagicMock(side_effect=lambda key: props if key == "r" else None)
    record.data = MagicMock(return_value={"r": props})
    return record


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_driver():
    """Create a mocked neo4j.AsyncDriver."""
    driver = AsyncMock()
    driver.verify_connectivity = AsyncMock()
    driver.close = AsyncMock()
    return driver


@pytest.fixture
def mock_session():
    """Create a mocked neo4j.AsyncSession."""
    session = AsyncMock()
    return session


@pytest.fixture
def engine(mock_driver, mock_session):
    """Create a Neo4jGraphEngine with a mocked driver, already 'connected'."""
    eng = Neo4jGraphEngine(
        uri="bolt://localhost:7687",
        user="neo4j",
        password="test",
    )
    eng._driver = mock_driver
    eng._connected = True

    # Set up session context manager
    mock_driver.session = MagicMock(return_value=mock_session)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    return eng


# ---------------------------------------------------------------------------
# Connection lifecycle tests
# ---------------------------------------------------------------------------


class TestConnectionLifecycle:
    async def test_connect_success(self, mock_driver):
        mock_neo4j = MagicMock()
        mock_neo4j.AsyncGraphDatabase.driver.return_value = mock_driver

        # Mock session for index creation
        sess = AsyncMock()
        sess.__aenter__ = AsyncMock(return_value=sess)
        sess.__aexit__ = AsyncMock(return_value=False)
        sess.run = AsyncMock()
        mock_driver.session = MagicMock(return_value=sess)

        with patch("twin_core.neo4j_graph_engine.neo4j", mock_neo4j):
            eng = Neo4jGraphEngine(uri="bolt://test:7687")
            await eng.connect()

        assert eng._connected is True
        mock_driver.verify_connectivity.assert_awaited_once()

    async def test_connect_failure(self, mock_driver):
        mock_neo4j = MagicMock()
        mock_neo4j.AsyncGraphDatabase.driver.return_value = mock_driver
        mock_driver.verify_connectivity = AsyncMock(side_effect=Exception("Connection refused"))

        with patch("twin_core.neo4j_graph_engine.neo4j", mock_neo4j):
            eng = Neo4jGraphEngine(uri="bolt://bad:7687")
            with pytest.raises(Neo4jConnectionError, match="Connection refused"):
                await eng.connect()

        assert eng._connected is False

    async def test_connect_without_neo4j_package(self):
        with patch("twin_core.neo4j_graph_engine.neo4j", None):
            eng = Neo4jGraphEngine()
            with pytest.raises(Neo4jConnectionError, match="not installed"):
                await eng.connect()

    async def test_close(self, engine, mock_driver):
        await engine.close()
        assert engine._connected is False
        mock_driver.close.assert_awaited_once()

    async def test_health_check_healthy(self, engine, mock_driver):
        result = await engine.health_check()
        assert result is True

    async def test_health_check_unhealthy(self, mock_driver):
        eng = Neo4jGraphEngine()
        eng._driver = mock_driver
        eng._connected = True
        mock_driver.verify_connectivity = AsyncMock(side_effect=Exception("down"))
        result = await eng.health_check()
        assert result is False

    async def test_health_check_not_connected(self):
        eng = Neo4jGraphEngine()
        result = await eng.health_check()
        assert result is False

    def test_assert_connected_raises_when_not_connected(self):
        eng = Neo4jGraphEngine()
        with pytest.raises(Neo4jConnectionError, match="Not connected"):
            eng._assert_connected()


# ---------------------------------------------------------------------------
# Node operation tests
# ---------------------------------------------------------------------------


class TestNodeOperations:
    async def test_add_node(self, engine, mock_session):
        artifact = _make_artifact()

        # First run: check existence (returns None = not found)
        mock_result_check = AsyncMock()
        mock_result_check.single = AsyncMock(return_value=None)

        # Second run: create node
        mock_result_create = AsyncMock()

        mock_session.run = AsyncMock(side_effect=[mock_result_check, mock_result_create])

        result = await engine.add_node(artifact)
        assert result.id == artifact.id
        assert result.name == "test"
        assert mock_session.run.await_count == 2

    async def test_add_node_duplicate_raises_value_error(self, engine, mock_session):
        artifact = _make_artifact()

        # Existence check returns a record (node exists)
        mock_result = AsyncMock()
        mock_result.single = AsyncMock(return_value=MagicMock())
        mock_session.run = AsyncMock(return_value=mock_result)

        with pytest.raises(ValueError, match="already exists"):
            await engine.add_node(artifact)

    async def test_get_node_found(self, engine, mock_session):
        artifact = _make_artifact()
        record = _make_mock_record(artifact)

        mock_result = AsyncMock()
        mock_result.single = AsyncMock(return_value=record)
        mock_session.run = AsyncMock(return_value=mock_result)

        result = await engine.get_node(artifact.id)
        assert result is not None
        assert result.id == artifact.id

    async def test_get_node_not_found(self, engine, mock_session):
        mock_result = AsyncMock()
        mock_result.single = AsyncMock(return_value=None)
        mock_session.run = AsyncMock(return_value=mock_result)

        result = await engine.get_node(uuid4())
        assert result is None

    async def test_update_node(self, engine, mock_session):
        artifact = _make_artifact()
        record = _make_mock_record(artifact)

        # get_node call (for current state)
        mock_result_get = AsyncMock()
        mock_result_get.single = AsyncMock(return_value=record)

        # update call
        mock_result_update = AsyncMock()

        # get_node call (for re-fetch after update)
        updated_artifact = artifact.model_copy(update={"name": "updated"})
        record_updated = _make_mock_record(updated_artifact)
        mock_result_refetch = AsyncMock()
        mock_result_refetch.single = AsyncMock(return_value=record_updated)

        mock_session.run = AsyncMock(
            side_effect=[
                mock_result_get,
                mock_result_update,
                mock_result_refetch,
            ]
        )

        result = await engine.update_node(artifact.id, {"name": "updated"})
        assert result is not None
        assert result.name == "updated"

    async def test_update_node_not_found_raises_key_error(self, engine, mock_session):
        mock_result = AsyncMock()
        mock_result.single = AsyncMock(return_value=None)
        mock_session.run = AsyncMock(return_value=mock_result)

        with pytest.raises(KeyError, match="not found"):
            await engine.update_node(uuid4(), {"name": "x"})

    async def test_delete_node(self, engine, mock_session):
        mock_result = AsyncMock()
        mock_record = MagicMock()
        mock_record.__getitem__ = MagicMock(return_value=1)
        mock_result.single = AsyncMock(return_value=mock_record)
        mock_session.run = AsyncMock(return_value=mock_result)

        result = await engine.delete_node(uuid4())
        assert result is True

    async def test_delete_node_not_found(self, engine, mock_session):
        mock_result = AsyncMock()
        mock_record = MagicMock()
        mock_record.__getitem__ = MagicMock(return_value=0)
        mock_result.single = AsyncMock(return_value=mock_record)
        mock_session.run = AsyncMock(return_value=mock_result)

        result = await engine.delete_node(uuid4())
        assert result is False

    async def test_list_nodes_all(self, engine, mock_session):
        a1 = _make_artifact("a")
        a2 = _make_artifact("b")
        props1 = Neo4jGraphEngine._node_to_props(a1)
        props2 = Neo4jGraphEngine._node_to_props(a2)

        mock_result = AsyncMock()
        mock_result.data = AsyncMock(return_value=[{"n": props1}, {"n": props2}])
        mock_session.run = AsyncMock(return_value=mock_result)

        result = await engine.list_nodes()
        assert len(result) == 2

    async def test_list_nodes_filtered_by_type(self, engine, mock_session):
        a1 = _make_artifact("a")
        props1 = Neo4jGraphEngine._node_to_props(a1)

        mock_result = AsyncMock()
        mock_result.data = AsyncMock(return_value=[{"n": props1}])
        mock_session.run = AsyncMock(return_value=mock_result)

        result = await engine.list_nodes(node_type=NodeType.ARTIFACT)
        assert len(result) == 1

        # Verify the query included the WHERE clause
        call_args = mock_session.run.call_args
        assert "node_type" in call_args.kwargs


# ---------------------------------------------------------------------------
# Edge operation tests
# ---------------------------------------------------------------------------


class TestEdgeOperations:
    async def test_add_edge(self, engine, mock_session):
        a = _make_artifact("a")
        b = _make_artifact("b")
        edge = EdgeBase(
            source_id=a.id,
            target_id=b.id,
            edge_type=EdgeType.DEPENDS_ON,
        )

        # Source check, target check, create
        mock_source_result = AsyncMock()
        mock_source_result.single = AsyncMock(return_value=MagicMock())

        mock_target_result = AsyncMock()
        mock_target_result.single = AsyncMock(return_value=MagicMock())

        mock_create_result = AsyncMock()

        mock_session.run = AsyncMock(
            side_effect=[mock_source_result, mock_target_result, mock_create_result]
        )

        result = await engine.add_edge(edge)
        assert result.source_id == a.id
        assert result.target_id == b.id
        assert result.edge_type == EdgeType.DEPENDS_ON

    async def test_add_edge_source_not_found(self, engine, mock_session):
        edge = EdgeBase(
            source_id=uuid4(),
            target_id=uuid4(),
            edge_type=EdgeType.DEPENDS_ON,
        )

        mock_result = AsyncMock()
        mock_result.single = AsyncMock(return_value=None)
        mock_session.run = AsyncMock(return_value=mock_result)

        with pytest.raises(ValueError, match="Source node"):
            await engine.add_edge(edge)

    async def test_add_edge_target_not_found(self, engine, mock_session):
        edge = EdgeBase(
            source_id=uuid4(),
            target_id=uuid4(),
            edge_type=EdgeType.DEPENDS_ON,
        )

        # Source found, target not found
        mock_source_result = AsyncMock()
        mock_source_result.single = AsyncMock(return_value=MagicMock())

        mock_target_result = AsyncMock()
        mock_target_result.single = AsyncMock(return_value=None)

        mock_session.run = AsyncMock(side_effect=[mock_source_result, mock_target_result])

        with pytest.raises(ValueError, match="Target node"):
            await engine.add_edge(edge)

    async def test_get_edges_outgoing(self, engine, mock_session):
        a = _make_artifact("a")
        b = _make_artifact("b")
        edge = EdgeBase(
            source_id=a.id,
            target_id=b.id,
            edge_type=EdgeType.DEPENDS_ON,
        )
        edge_props = Neo4jGraphEngine._edge_to_props(edge)

        mock_result = AsyncMock()
        mock_result.data = AsyncMock(return_value=[{"r": edge_props}])
        mock_session.run = AsyncMock(return_value=mock_result)

        result = await engine.get_edges(a.id, direction="outgoing")
        assert len(result) == 1
        assert result[0].edge_type == EdgeType.DEPENDS_ON

    async def test_get_edges_both_directions(self, engine, mock_session):
        a_id = uuid4()
        edge_out = EdgeBase(source_id=a_id, target_id=uuid4(), edge_type=EdgeType.DEPENDS_ON)
        edge_in = EdgeBase(source_id=uuid4(), target_id=a_id, edge_type=EdgeType.VALIDATES)

        mock_out = AsyncMock()
        mock_out.data = AsyncMock(return_value=[{"r": Neo4jGraphEngine._edge_to_props(edge_out)}])
        mock_in = AsyncMock()
        mock_in.data = AsyncMock(return_value=[{"r": Neo4jGraphEngine._edge_to_props(edge_in)}])
        mock_session.run = AsyncMock(side_effect=[mock_out, mock_in])

        result = await engine.get_edges(a_id, direction="both")
        assert len(result) == 2

    async def test_remove_edge(self, engine, mock_session):
        mock_result = AsyncMock()
        mock_record = MagicMock()
        mock_record.__getitem__ = MagicMock(return_value=1)
        mock_result.single = AsyncMock(return_value=mock_record)
        mock_session.run = AsyncMock(return_value=mock_result)

        result = await engine.remove_edge(uuid4(), uuid4(), EdgeType.DEPENDS_ON)
        assert result is True

    async def test_remove_edge_not_found(self, engine, mock_session):
        mock_result = AsyncMock()
        mock_record = MagicMock()
        mock_record.__getitem__ = MagicMock(return_value=0)
        mock_result.single = AsyncMock(return_value=mock_record)
        mock_session.run = AsyncMock(return_value=mock_result)

        result = await engine.remove_edge(uuid4(), uuid4(), EdgeType.DEPENDS_ON)
        assert result is False


# ---------------------------------------------------------------------------
# Traversal query tests
# ---------------------------------------------------------------------------


class TestTraversalQueries:
    async def test_get_neighbors(self, engine, mock_session):
        a = _make_artifact("a")
        b = _make_artifact("b")
        edge = EdgeBase(source_id=a.id, target_id=b.id, edge_type=EdgeType.DEPENDS_ON)

        # get_edges returns one edge
        edge_props = Neo4jGraphEngine._edge_to_props(edge)
        mock_edges = AsyncMock()
        mock_edges.data = AsyncMock(return_value=[{"r": edge_props}])

        # get_node for neighbor
        b_record = _make_mock_record(b)
        mock_node = AsyncMock()
        mock_node.single = AsyncMock(return_value=b_record)

        mock_session.run = AsyncMock(side_effect=[mock_edges, mock_node])

        result = await engine.get_neighbors(a.id, direction="outgoing")
        assert len(result) == 1
        assert result[0].id == b.id

    async def test_get_subgraph(self, engine, mock_session):
        a = _make_artifact("a")
        b = _make_artifact("b")
        edge = EdgeBase(source_id=a.id, target_id=b.id, edge_type=EdgeType.DEPENDS_ON)

        # get_node for root
        a_record = _make_mock_record(a)
        mock_root = AsyncMock()
        mock_root.single = AsyncMock(return_value=a_record)

        # get_edges for root (returns one edge)
        edge_props = Neo4jGraphEngine._edge_to_props(edge)
        mock_edges_root = AsyncMock()
        mock_edges_root.data = AsyncMock(return_value=[{"r": edge_props}])

        # get_node for b
        b_record = _make_mock_record(b)
        mock_b_node = AsyncMock()
        mock_b_node.single = AsyncMock(return_value=b_record)

        # get_edges for b (depth 1 of 2, so we continue)
        mock_edges_b = AsyncMock()
        mock_edges_b.data = AsyncMock(return_value=[])

        mock_session.run = AsyncMock(
            side_effect=[
                mock_root,  # get_node(root_id)
                mock_edges_root,  # get_edges(root, outgoing)
                mock_b_node,  # get_node(b.id)
                mock_edges_b,  # get_edges(b, outgoing)
            ]
        )

        sg = await engine.get_subgraph(a.id, depth=2)
        assert sg.root_id == a.id
        assert len(sg.nodes) == 2
        assert len(sg.edges) == 1

    async def test_get_subgraph_root_not_found(self, engine, mock_session):
        mock_result = AsyncMock()
        mock_result.single = AsyncMock(return_value=None)
        mock_session.run = AsyncMock(return_value=mock_result)

        with pytest.raises(KeyError, match="not found"):
            await engine.get_subgraph(uuid4())

    async def test_traverse(self, engine, mock_session):
        a = _make_artifact("a")
        b = _make_artifact("b")
        edge = EdgeBase(source_id=a.id, target_id=b.id, edge_type=EdgeType.DEPENDS_ON)

        # get_node for root
        a_record = _make_mock_record(a)
        mock_root = AsyncMock()
        mock_root.single = AsyncMock(return_value=a_record)

        # get_edges for a (returns one edge)
        edge_props = Neo4jGraphEngine._edge_to_props(edge)
        mock_edges_a = AsyncMock()
        mock_edges_a.data = AsyncMock(return_value=[{"r": edge_props}])

        # get_edges for b (no children)
        mock_edges_b = AsyncMock()
        mock_edges_b.data = AsyncMock(return_value=[])

        mock_session.run = AsyncMock(side_effect=[mock_root, mock_edges_a, mock_edges_b])

        paths = await engine.traverse(a.id, edge_types=[EdgeType.DEPENDS_ON], max_depth=5)
        assert len(paths) == 1
        assert paths[0] == [a.id, b.id]

    async def test_traverse_root_not_found(self, engine, mock_session):
        mock_result = AsyncMock()
        mock_result.single = AsyncMock(return_value=None)
        mock_session.run = AsyncMock(return_value=mock_result)

        with pytest.raises(KeyError, match="not found"):
            await engine.traverse(uuid4(), edge_types=[EdgeType.DEPENDS_ON])


# ---------------------------------------------------------------------------
# Cypher query tests
# ---------------------------------------------------------------------------


class TestCypherQuery:
    async def test_query_cypher(self, engine, mock_session):
        mock_result = AsyncMock()
        mock_result.data = AsyncMock(return_value=[{"name": "test"}, {"name": "other"}])
        mock_session.run = AsyncMock(return_value=mock_result)

        result = await engine.query_cypher("MATCH (n) RETURN n.name AS name", params={})
        assert len(result) == 2
        assert result[0]["name"] == "test"

    async def test_query_cypher_failure(self, engine, mock_session):
        mock_session.run = AsyncMock(side_effect=Exception("Syntax error"))

        with pytest.raises(Neo4jQueryError, match="Cypher query failed"):
            await engine.query_cypher("INVALID CYPHER")


# ---------------------------------------------------------------------------
# Error handling tests
# ---------------------------------------------------------------------------


class TestErrorHandling:
    async def test_operations_fail_when_not_connected(self):
        eng = Neo4jGraphEngine()
        artifact = _make_artifact()

        with pytest.raises(Neo4jConnectionError):
            await eng.add_node(artifact)

        with pytest.raises(Neo4jConnectionError):
            await eng.get_node(uuid4())

        with pytest.raises(Neo4jConnectionError):
            await eng.update_node(uuid4(), {})

        with pytest.raises(Neo4jConnectionError):
            await eng.delete_node(uuid4())

        with pytest.raises(Neo4jConnectionError):
            await eng.list_nodes()

        with pytest.raises(Neo4jConnectionError):
            await eng.add_edge(
                EdgeBase(
                    source_id=uuid4(),
                    target_id=uuid4(),
                    edge_type=EdgeType.DEPENDS_ON,
                )
            )

        with pytest.raises(Neo4jConnectionError):
            await eng.get_edges(uuid4())

        with pytest.raises(Neo4jConnectionError):
            await eng.remove_edge(uuid4(), uuid4(), EdgeType.DEPENDS_ON)

        with pytest.raises(Neo4jConnectionError):
            await eng.get_neighbors(uuid4())

        with pytest.raises(Neo4jConnectionError):
            await eng.get_subgraph(uuid4())

        with pytest.raises(Neo4jConnectionError):
            await eng.traverse(uuid4(), edge_types=[EdgeType.DEPENDS_ON])

        with pytest.raises(Neo4jConnectionError):
            await eng.query_cypher("MATCH (n) RETURN n")

    async def test_driver_error_raises_query_error(self, engine, mock_session):
        mock_session.run = AsyncMock(side_effect=RuntimeError("driver crash"))

        with pytest.raises(Neo4jQueryError):
            await engine.get_node(uuid4())


# ---------------------------------------------------------------------------
# Serialization round-trip tests
# ---------------------------------------------------------------------------


class TestSerialization:
    def test_node_round_trip(self):
        artifact = _make_artifact()
        props = Neo4jGraphEngine._node_to_props(artifact)
        restored = Neo4jGraphEngine._props_to_node(props)
        assert restored.id == artifact.id
        assert restored.node_type == NodeType.ARTIFACT

    def test_edge_round_trip(self):
        edge = EdgeBase(
            source_id=uuid4(),
            target_id=uuid4(),
            edge_type=EdgeType.DEPENDS_ON,
            metadata={"weight": 1.0},
        )
        props = Neo4jGraphEngine._edge_to_props(edge)
        restored = Neo4jGraphEngine._props_to_edge(props)
        assert restored.source_id == edge.source_id
        assert restored.target_id == edge.target_id
        assert restored.edge_type == EdgeType.DEPENDS_ON
