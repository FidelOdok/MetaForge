"""Neo4j-backed graph engine for the Digital Twin.

Implements the ``GraphEngine`` ABC using the Neo4j async Python driver.
All operations are traced via OpenTelemetry and logged with structlog.
"""

from __future__ import annotations

import json
import time
from collections import deque
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog

from observability.tracing import get_tracer
from twin_core.graph_engine import GraphEngine
from twin_core.models.base import EdgeBase, NodeBase
from twin_core.models.enums import EdgeType, NodeType
from twin_core.models.relationship import SubGraph

try:
    import neo4j
except ImportError:
    neo4j = None  # type: ignore[assignment]

logger = structlog.get_logger(__name__)
tracer = get_tracer("twin_core.neo4j_graph_engine")


class Neo4jConnectionError(Exception):
    """Raised when a Neo4j connection cannot be established or has been lost."""


class Neo4jQueryError(Exception):
    """Raised when a Neo4j query fails."""


class Neo4jGraphEngine(GraphEngine):
    """Neo4j-backed implementation of the Digital Twin graph engine.

    Uses the ``neo4j`` async Python driver for all database operations.
    Gracefully handles connection lifecycle and provides full observability.
    """

    def __init__(
        self,
        uri: str = "bolt://localhost:7687",
        user: str = "neo4j",
        password: str = "password",
        database: str = "neo4j",
    ) -> None:
        self._uri = uri
        self._user = user
        self._password = password
        self._database = database
        self._driver: Any = None
        self._connected = False

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Establish connection to Neo4j and create indexes/constraints."""
        if neo4j is None:
            raise Neo4jConnectionError(
                "neo4j package is not installed. Install with: pip install metaforge[neo4j]"
            )

        with tracer.start_as_current_span("neo4j.connect") as span:
            span.set_attribute("db.system", "neo4j")
            span.set_attribute("db.uri", self._uri)
            try:
                self._driver = neo4j.AsyncGraphDatabase.driver(
                    self._uri,
                    auth=(self._user, self._password),
                )
                # Verify connectivity
                await self._driver.verify_connectivity()
                self._connected = True
                logger.info(
                    "neo4j_connected",
                    uri=self._uri,
                    database=self._database,
                )
                await self._ensure_indexes()
            except Exception as exc:
                span.record_exception(exc)
                self._connected = False
                logger.error(
                    "neo4j_connection_failed",
                    uri=self._uri,
                    error=str(exc),
                )
                raise Neo4jConnectionError(
                    f"Failed to connect to Neo4j at {self._uri}: {exc}"
                ) from exc

    async def close(self) -> None:
        """Close the Neo4j driver connection."""
        if self._driver is not None:
            await self._driver.close()
            self._connected = False
            logger.info("neo4j_disconnected", uri=self._uri)

    async def health_check(self) -> bool:
        """Return True if the Neo4j connection is healthy."""
        if self._driver is None or not self._connected:
            return False
        try:
            await self._driver.verify_connectivity()
            return True
        except Exception:
            return False

    async def _ensure_indexes(self) -> None:
        """Create indexes and constraints for the graph schema."""
        statements = [
            "CREATE CONSTRAINT node_id_unique IF NOT EXISTS FOR (n:Node) REQUIRE n.id IS UNIQUE",
            "CREATE INDEX node_type_index IF NOT EXISTS FOR (n:Node) ON (n.node_type)",
            "CREATE INDEX edge_type_index IF NOT EXISTS FOR ()-[r:EDGE]-() ON (r.edge_type)",
        ]
        async with self._driver.session(database=self._database) as session:
            for stmt in statements:
                try:
                    await session.run(stmt)
                    logger.debug("neo4j_index_created", statement=stmt)
                except Exception as exc:
                    logger.warning(
                        "neo4j_index_creation_skipped",
                        statement=stmt,
                        error=str(exc),
                    )

    def _assert_connected(self) -> None:
        """Raise if not connected."""
        if not self._connected or self._driver is None:
            raise Neo4jConnectionError("Not connected to Neo4j. Call connect() first.")

    # ------------------------------------------------------------------
    # Serialization helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _node_to_props(node: NodeBase) -> dict[str, Any]:
        """Convert a NodeBase to a dict suitable for Neo4j properties."""
        data = node.model_dump(mode="json")
        # Flatten — Neo4j properties must be primitives or lists of primitives
        result: dict[str, Any] = {}
        for key, value in data.items():
            if isinstance(value, dict):
                result[key] = json.dumps(value)
            elif isinstance(value, list):
                result[key] = json.dumps(value)
            else:
                result[key] = value
        # Ensure id is stored as a string
        result["id"] = str(node.id)
        return result

    @staticmethod
    def _props_to_node(props: dict[str, Any]) -> NodeBase:
        """Reconstruct a NodeBase from Neo4j node properties."""
        data: dict[str, Any] = dict(props)
        # Deserialize JSON strings back to dicts/lists
        for key, value in data.items():
            if isinstance(value, str) and value.startswith(("{", "[")):
                try:
                    data[key] = json.loads(value)
                except (json.JSONDecodeError, ValueError):
                    pass

        # Use the node_type to determine which model to construct
        node_type = data.get("node_type", "")
        if node_type == NodeType.WORK_PRODUCT:
            from twin_core.models.work_product import WorkProduct

            return WorkProduct.model_validate(data)
        elif node_type == NodeType.CONSTRAINT:
            from twin_core.models.constraint import Constraint

            return Constraint.model_validate(data)
        elif node_type == NodeType.COMPONENT:
            from twin_core.models.component import Component

            return Component.model_validate(data)
        elif node_type == NodeType.AGENT:
            from twin_core.models.agent import AgentNode

            return AgentNode.model_validate(data)
        elif node_type == NodeType.VERSION:
            from twin_core.models.version import Version

            return Version.model_validate(data)
        else:
            return NodeBase.model_validate(data)

    @staticmethod
    def _edge_to_props(edge: EdgeBase) -> dict[str, Any]:
        """Convert an EdgeBase to a dict suitable for Neo4j relationship properties."""
        data = edge.model_dump(mode="json")
        result: dict[str, Any] = {}
        for key, value in data.items():
            if isinstance(value, dict):
                result[key] = json.dumps(value)
            elif isinstance(value, list):
                result[key] = json.dumps(value)
            else:
                result[key] = value
        result["source_id"] = str(edge.source_id)
        result["target_id"] = str(edge.target_id)
        return result

    @staticmethod
    def _props_to_edge(props: dict[str, Any]) -> EdgeBase:
        """Reconstruct an EdgeBase from Neo4j relationship properties."""
        data: dict[str, Any] = dict(props)
        for key, value in data.items():
            if isinstance(value, str) and value.startswith(("{", "[")):
                try:
                    data[key] = json.loads(value)
                except (json.JSONDecodeError, ValueError):
                    pass
        return EdgeBase.model_validate(data)

    # ------------------------------------------------------------------
    # Node operations
    # ------------------------------------------------------------------

    async def add_node(self, node: NodeBase) -> NodeBase:
        """Add a node to the graph. Raises ValueError if ID already exists."""
        self._assert_connected()
        t0 = time.monotonic()
        with tracer.start_as_current_span("neo4j.add_node") as span:
            span.set_attribute("db.operation", "add_node")
            span.set_attribute("node.id", str(node.id))
            span.set_attribute("node.type", str(node.node_type))
            try:
                props = self._node_to_props(node)
                async with self._driver.session(database=self._database) as session:
                    # Check existence
                    result = await session.run(
                        "MATCH (n:Node {id: $id}) RETURN n",
                        id=str(node.id),
                    )
                    record = await result.single()
                    if record is not None:
                        raise ValueError(f"Node with ID {node.id} already exists")

                    # Create node with both :Node and :NodeType labels
                    label = node.node_type.value.capitalize()
                    await session.run(
                        f"CREATE (n:Node:{label} $props)",  # noqa: S608
                        props=props,
                    )
                logger.info(
                    "neo4j_node_added",
                    node_id=str(node.id),
                    node_type=str(node.node_type),
                    duration_ms=round((time.monotonic() - t0) * 1000, 2),
                )
                return node
            except ValueError:
                raise
            except Exception as exc:
                span.record_exception(exc)
                logger.error(
                    "neo4j_add_node_failed",
                    node_id=str(node.id),
                    error=str(exc),
                )
                raise Neo4jQueryError(f"Failed to add node: {exc}") from exc

    async def get_node(self, node_id: UUID) -> NodeBase | None:
        """Retrieve a node by ID, or None if not found."""
        self._assert_connected()
        with tracer.start_as_current_span("neo4j.get_node") as span:
            span.set_attribute("db.operation", "get_node")
            span.set_attribute("node.id", str(node_id))
            try:
                async with self._driver.session(database=self._database) as session:
                    result = await session.run(
                        "MATCH (n:Node {id: $id}) RETURN n",
                        id=str(node_id),
                    )
                    record = await result.single()
                    if record is None:
                        return None
                    return self._props_to_node(dict(record["n"]))
            except Exception as exc:
                span.record_exception(exc)
                logger.error(
                    "neo4j_get_node_failed",
                    node_id=str(node_id),
                    error=str(exc),
                )
                raise Neo4jQueryError(f"Failed to get node: {exc}") from exc

    async def update_node(self, node_id: UUID, updates: dict) -> NodeBase:
        """Update a node's fields. Raises KeyError if node not found."""
        self._assert_connected()
        t0 = time.monotonic()
        with tracer.start_as_current_span("neo4j.update_node") as span:
            span.set_attribute("db.operation", "update_node")
            span.set_attribute("node.id", str(node_id))
            try:
                # Get current node first
                current = await self.get_node(node_id)
                if current is None:
                    raise KeyError(f"Node {node_id} not found")

                if hasattr(current, "updated_at") and "updated_at" not in updates:
                    updates["updated_at"] = datetime.now(UTC).isoformat()

                # Serialize update values
                serialized: dict[str, Any] = {}
                for key, value in updates.items():
                    if isinstance(value, dict):
                        serialized[key] = json.dumps(value)
                    elif isinstance(value, list):
                        serialized[key] = json.dumps(value)
                    elif isinstance(value, UUID):
                        serialized[key] = str(value)
                    elif isinstance(value, datetime):
                        serialized[key] = value.isoformat()
                    else:
                        serialized[key] = value

                async with self._driver.session(database=self._database) as session:
                    await session.run(
                        "MATCH (n:Node {id: $id}) SET n += $updates",
                        id=str(node_id),
                        updates=serialized,
                    )

                # Re-fetch to return updated node
                updated = await self.get_node(node_id)
                if updated is None:
                    raise KeyError(f"Node {node_id} not found after update")

                logger.info(
                    "neo4j_node_updated",
                    node_id=str(node_id),
                    fields=list(updates.keys()),
                    duration_ms=round((time.monotonic() - t0) * 1000, 2),
                )
                return updated
            except KeyError:
                raise
            except Neo4jQueryError:
                raise
            except Exception as exc:
                span.record_exception(exc)
                logger.error(
                    "neo4j_update_node_failed",
                    node_id=str(node_id),
                    error=str(exc),
                )
                raise Neo4jQueryError(f"Failed to update node: {exc}") from exc

    async def delete_node(self, node_id: UUID) -> bool:
        """Delete a node and all its connected edges. Returns False if not found."""
        self._assert_connected()
        t0 = time.monotonic()
        with tracer.start_as_current_span("neo4j.delete_node") as span:
            span.set_attribute("db.operation", "delete_node")
            span.set_attribute("node.id", str(node_id))
            try:
                async with self._driver.session(database=self._database) as session:
                    result = await session.run(
                        "MATCH (n:Node {id: $id}) DETACH DELETE n RETURN count(n) AS deleted",
                        id=str(node_id),
                    )
                    record = await result.single()
                    deleted = record["deleted"] > 0 if record else False

                logger.info(
                    "neo4j_node_deleted",
                    node_id=str(node_id),
                    deleted=deleted,
                    duration_ms=round((time.monotonic() - t0) * 1000, 2),
                )
                return deleted
            except Exception as exc:
                span.record_exception(exc)
                logger.error(
                    "neo4j_delete_node_failed",
                    node_id=str(node_id),
                    error=str(exc),
                )
                raise Neo4jQueryError(f"Failed to delete node: {exc}") from exc

    async def list_nodes(
        self,
        node_type: NodeType | None = None,
        filters: dict | None = None,
    ) -> list[NodeBase]:
        """List nodes, optionally filtered by type and field values."""
        self._assert_connected()
        with tracer.start_as_current_span("neo4j.list_nodes") as span:
            span.set_attribute("db.operation", "list_nodes")
            if node_type:
                span.set_attribute("node.type", str(node_type))
            try:
                where_clauses: list[str] = []
                params: dict[str, Any] = {}

                if node_type is not None:
                    where_clauses.append("n.node_type = $node_type")
                    params["node_type"] = str(node_type)

                if filters:
                    for i, (key, value) in enumerate(filters.items()):
                        param_name = f"filter_{i}"
                        where_clauses.append(f"n.{key} = ${param_name}")
                        params[param_name] = str(value) if isinstance(value, UUID) else value

                where = " WHERE " + " AND ".join(where_clauses) if where_clauses else ""
                query = f"MATCH (n:Node){where} RETURN n"  # noqa: S608

                async with self._driver.session(database=self._database) as session:
                    result = await session.run(query, **params)
                    records = await result.data()
                    nodes = [self._props_to_node(dict(r["n"])) for r in records]

                span.set_attribute("neo4j.result_count", len(nodes))
                return nodes
            except Exception as exc:
                span.record_exception(exc)
                logger.error("neo4j_list_nodes_failed", error=str(exc))
                raise Neo4jQueryError(f"Failed to list nodes: {exc}") from exc

    # ------------------------------------------------------------------
    # Edge operations
    # ------------------------------------------------------------------

    async def add_edge(self, edge: EdgeBase) -> EdgeBase:
        """Add an edge. Raises ValueError if source or target node doesn't exist."""
        self._assert_connected()
        t0 = time.monotonic()
        with tracer.start_as_current_span("neo4j.add_edge") as span:
            span.set_attribute("db.operation", "add_edge")
            span.set_attribute("edge.type", str(edge.edge_type))
            span.set_attribute("edge.source_id", str(edge.source_id))
            span.set_attribute("edge.target_id", str(edge.target_id))
            try:
                props = self._edge_to_props(edge)
                async with self._driver.session(database=self._database) as session:
                    # Verify both nodes exist
                    result = await session.run(
                        "MATCH (s:Node {id: $sid}) RETURN s",
                        sid=str(edge.source_id),
                    )
                    if await result.single() is None:
                        raise ValueError(f"Source node {edge.source_id} does not exist")

                    result = await session.run(
                        "MATCH (t:Node {id: $tid}) RETURN t",
                        tid=str(edge.target_id),
                    )
                    if await result.single() is None:
                        raise ValueError(f"Target node {edge.target_id} does not exist")

                    await session.run(
                        "MATCH (s:Node {id: $sid}), (t:Node {id: $tid}) "
                        "CREATE (s)-[r:EDGE $props]->(t)",
                        sid=str(edge.source_id),
                        tid=str(edge.target_id),
                        props=props,
                    )

                logger.info(
                    "neo4j_edge_added",
                    source_id=str(edge.source_id),
                    target_id=str(edge.target_id),
                    edge_type=str(edge.edge_type),
                    duration_ms=round((time.monotonic() - t0) * 1000, 2),
                )
                return edge
            except ValueError:
                raise
            except Exception as exc:
                span.record_exception(exc)
                logger.error("neo4j_add_edge_failed", error=str(exc))
                raise Neo4jQueryError(f"Failed to add edge: {exc}") from exc

    async def get_edges(
        self,
        node_id: UUID,
        direction: str = "outgoing",
        edge_type: EdgeType | None = None,
    ) -> list[EdgeBase]:
        """Get edges connected to a node. Direction: 'outgoing', 'incoming', or 'both'."""
        self._assert_connected()
        with tracer.start_as_current_span("neo4j.get_edges") as span:
            span.set_attribute("db.operation", "get_edges")
            span.set_attribute("node.id", str(node_id))
            span.set_attribute("edge.direction", direction)
            try:
                queries: list[str] = []
                params: dict[str, Any] = {"nid": str(node_id)}

                if edge_type is not None:
                    params["edge_type"] = str(edge_type)
                    type_filter = " AND r.edge_type = $edge_type"
                else:
                    type_filter = ""

                if direction in ("outgoing", "both"):
                    queries.append(
                        f"MATCH (n:Node {{id: $nid}})-[r:EDGE]->(t) "
                        f"WHERE true{type_filter} RETURN r"
                    )
                if direction in ("incoming", "both"):
                    queries.append(
                        f"MATCH (s)-[r:EDGE]->(n:Node {{id: $nid}}) "
                        f"WHERE true{type_filter} RETURN r"
                    )

                edges: list[EdgeBase] = []
                async with self._driver.session(database=self._database) as session:
                    for query in queries:
                        result = await session.run(query, **params)
                        records = await result.data()
                        for rec in records:
                            edges.append(self._props_to_edge(dict(rec["r"])))

                span.set_attribute("neo4j.result_count", len(edges))
                return edges
            except Exception as exc:
                span.record_exception(exc)
                logger.error("neo4j_get_edges_failed", error=str(exc))
                raise Neo4jQueryError(f"Failed to get edges: {exc}") from exc

    async def remove_edge(self, source_id: UUID, target_id: UUID, edge_type: EdgeType) -> bool:
        """Remove a specific edge. Returns False if not found."""
        self._assert_connected()
        t0 = time.monotonic()
        with tracer.start_as_current_span("neo4j.remove_edge") as span:
            span.set_attribute("db.operation", "remove_edge")
            span.set_attribute("edge.source_id", str(source_id))
            span.set_attribute("edge.target_id", str(target_id))
            span.set_attribute("edge.type", str(edge_type))
            try:
                async with self._driver.session(database=self._database) as session:
                    result = await session.run(
                        "MATCH (s:Node {id: $sid})-[r:EDGE {edge_type: $etype}]->"
                        "(t:Node {id: $tid}) DELETE r RETURN count(r) AS deleted",
                        sid=str(source_id),
                        tid=str(target_id),
                        etype=str(edge_type),
                    )
                    record = await result.single()
                    removed = record["deleted"] > 0 if record else False

                logger.info(
                    "neo4j_edge_removed",
                    source_id=str(source_id),
                    target_id=str(target_id),
                    edge_type=str(edge_type),
                    removed=removed,
                    duration_ms=round((time.monotonic() - t0) * 1000, 2),
                )
                return removed
            except Exception as exc:
                span.record_exception(exc)
                logger.error("neo4j_remove_edge_failed", error=str(exc))
                raise Neo4jQueryError(f"Failed to remove edge: {exc}") from exc

    # ------------------------------------------------------------------
    # Traversal queries
    # ------------------------------------------------------------------

    async def get_neighbors(
        self,
        node_id: UUID,
        edge_type: EdgeType | None = None,
        direction: str = "outgoing",
    ) -> list[NodeBase]:
        """Get nodes directly connected to the given node."""
        self._assert_connected()
        with tracer.start_as_current_span("neo4j.get_neighbors") as span:
            span.set_attribute("db.operation", "get_neighbors")
            span.set_attribute("node.id", str(node_id))
            try:
                edges = await self.get_edges(node_id, direction=direction, edge_type=edge_type)
                neighbor_ids: list[UUID] = []
                for edge in edges:
                    nid = edge.target_id if edge.source_id == node_id else edge.source_id
                    if nid not in neighbor_ids:
                        neighbor_ids.append(nid)

                neighbors: list[NodeBase] = []
                for nid in neighbor_ids:
                    node = await self.get_node(nid)
                    if node is not None:
                        neighbors.append(node)

                span.set_attribute("neo4j.result_count", len(neighbors))
                return neighbors
            except Neo4jQueryError:
                raise
            except Exception as exc:
                span.record_exception(exc)
                logger.error("neo4j_get_neighbors_failed", error=str(exc))
                raise Neo4jQueryError(f"Failed to get neighbors: {exc}") from exc

    async def get_subgraph(
        self,
        root_id: UUID,
        depth: int = 2,
        edge_types: list[EdgeType] | None = None,
    ) -> SubGraph:
        """BFS traversal from root, returning all nodes/edges within depth hops."""
        self._assert_connected()
        with tracer.start_as_current_span("neo4j.get_subgraph") as span:
            span.set_attribute("db.operation", "get_subgraph")
            span.set_attribute("node.id", str(root_id))
            span.set_attribute("traversal.depth", depth)
            try:
                root = await self.get_node(root_id)
                if root is None:
                    raise KeyError(f"Root node {root_id} not found")

                visited_nodes: dict[UUID, NodeBase] = {root_id: root}
                collected_edges: list[EdgeBase] = []
                queue: deque[tuple[UUID, int]] = deque([(root_id, 0)])

                while queue:
                    current_id, current_depth = queue.popleft()
                    if current_depth >= depth:
                        continue

                    edges = await self.get_edges(current_id, direction="outgoing")
                    for edge in edges:
                        if edge_types and edge.edge_type not in edge_types:
                            continue
                        collected_edges.append(edge)
                        if edge.target_id not in visited_nodes:
                            target = await self.get_node(edge.target_id)
                            if target is not None:
                                visited_nodes[edge.target_id] = target
                                queue.append((edge.target_id, current_depth + 1))

                span.set_attribute(
                    "neo4j.result_count",
                    len(visited_nodes) + len(collected_edges),
                )
                return SubGraph(
                    nodes=list(visited_nodes.values()),
                    edges=collected_edges,
                    root_id=root_id,
                    depth=depth,
                )
            except KeyError:
                raise
            except Neo4jQueryError:
                raise
            except Exception as exc:
                span.record_exception(exc)
                logger.error("neo4j_get_subgraph_failed", error=str(exc))
                raise Neo4jQueryError(f"Failed to get subgraph: {exc}") from exc

    async def traverse(
        self,
        root_id: UUID,
        edge_types: list[EdgeType],
        max_depth: int = 5,
    ) -> list[list[UUID]]:
        """Find all paths from root following the given edge types, up to max_depth."""
        self._assert_connected()
        with tracer.start_as_current_span("neo4j.traverse") as span:
            span.set_attribute("db.operation", "traverse")
            span.set_attribute("node.id", str(root_id))
            span.set_attribute("traversal.max_depth", max_depth)
            try:
                root = await self.get_node(root_id)
                if root is None:
                    raise KeyError(f"Root node {root_id} not found")

                paths: list[list[UUID]] = []
                stack: list[tuple[UUID, list[UUID]]] = [(root_id, [root_id])]

                while stack:
                    current_id, current_path = stack.pop()
                    if len(current_path) - 1 >= max_depth:
                        paths.append(current_path)
                        continue

                    edges = await self.get_edges(current_id, direction="outgoing")
                    has_children = False
                    for edge in edges:
                        if edge.edge_type not in edge_types:
                            continue
                        if edge.target_id in current_path:
                            continue  # Avoid cycles
                        has_children = True
                        stack.append(
                            (
                                edge.target_id,
                                current_path + [edge.target_id],
                            )
                        )

                    if not has_children and len(current_path) > 1:
                        paths.append(current_path)

                span.set_attribute("neo4j.result_count", len(paths))
                return paths
            except KeyError:
                raise
            except Neo4jQueryError:
                raise
            except Exception as exc:
                span.record_exception(exc)
                logger.error("neo4j_traverse_failed", error=str(exc))
                raise Neo4jQueryError(f"Failed to traverse: {exc}") from exc

    # ------------------------------------------------------------------
    # Cypher query support
    # ------------------------------------------------------------------

    async def query_cypher(
        self,
        query: str,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Execute a raw Cypher query and return results as list of dicts."""
        self._assert_connected()
        t0 = time.monotonic()
        with tracer.start_as_current_span("neo4j.query") as span:
            span.set_attribute("db.statement", query)
            span.set_attribute("db.operation", "cypher_query")
            try:
                async with self._driver.session(database=self._database) as session:
                    result = await session.run(query, **(params or {}))
                    records = await result.data()

                span.set_attribute("neo4j.result_count", len(records))
                logger.debug(
                    "neo4j_cypher_executed",
                    query=query[:200],
                    result_count=len(records),
                    duration_ms=round((time.monotonic() - t0) * 1000, 2),
                )
                return records
            except Exception as exc:
                span.record_exception(exc)
                logger.error(
                    "neo4j_cypher_failed",
                    query=query[:200],
                    error=str(exc),
                )
                raise Neo4jQueryError(f"Cypher query failed: {exc}") from exc
