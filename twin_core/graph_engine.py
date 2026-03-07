"""Graph engine — abstract interface and in-memory implementation for the Digital Twin."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections import defaultdict, deque
from datetime import UTC, datetime
from uuid import UUID

from twin_core.models.base import EdgeBase, NodeBase
from twin_core.models.enums import EdgeType, NodeType
from twin_core.models.relationship import SubGraph


class GraphEngine(ABC):
    """Abstract interface for Digital Twin graph storage and retrieval.

    All backends (in-memory, Neo4j) implement this contract.
    """

    # --- Node operations ---

    @abstractmethod
    async def add_node(self, node: NodeBase) -> NodeBase:
        """Add a node to the graph. Raises ValueError if ID already exists."""
        ...

    @abstractmethod
    async def get_node(self, node_id: UUID) -> NodeBase | None:
        """Retrieve a node by ID, or None if not found."""
        ...

    @abstractmethod
    async def update_node(self, node_id: UUID, updates: dict) -> NodeBase:
        """Update a node's fields. Raises KeyError if node not found."""
        ...

    @abstractmethod
    async def delete_node(self, node_id: UUID) -> bool:
        """Delete a node and all its connected edges. Returns False if not found."""
        ...

    @abstractmethod
    async def list_nodes(
        self,
        node_type: NodeType | None = None,
        filters: dict | None = None,
    ) -> list[NodeBase]:
        """List nodes, optionally filtered by type and field values."""
        ...

    # --- Edge operations ---

    @abstractmethod
    async def add_edge(self, edge: EdgeBase) -> EdgeBase:
        """Add an edge. Raises ValueError if source or target node doesn't exist."""
        ...

    @abstractmethod
    async def get_edges(
        self,
        node_id: UUID,
        direction: str = "outgoing",
        edge_type: EdgeType | None = None,
    ) -> list[EdgeBase]:
        """Get edges connected to a node. Direction: 'outgoing', 'incoming', or 'both'."""
        ...

    @abstractmethod
    async def remove_edge(
        self, source_id: UUID, target_id: UUID, edge_type: EdgeType
    ) -> bool:
        """Remove a specific edge. Returns False if not found."""
        ...

    # --- Traversal queries ---

    @abstractmethod
    async def get_neighbors(
        self,
        node_id: UUID,
        edge_type: EdgeType | None = None,
        direction: str = "outgoing",
    ) -> list[NodeBase]:
        """Get nodes directly connected to the given node."""
        ...

    @abstractmethod
    async def get_subgraph(
        self,
        root_id: UUID,
        depth: int = 2,
        edge_types: list[EdgeType] | None = None,
    ) -> SubGraph:
        """BFS traversal from root, returning all nodes/edges within depth hops."""
        ...

    @abstractmethod
    async def traverse(
        self,
        root_id: UUID,
        edge_types: list[EdgeType],
        max_depth: int = 5,
    ) -> list[list[UUID]]:
        """Find all paths from root following the given edge types, up to max_depth."""
        ...


class InMemoryGraphEngine(GraphEngine):
    """Dict-based in-memory graph engine for development and testing."""

    def __init__(self, collector: "MetricsCollector | None" = None) -> None:
        from observability.metrics import MetricsCollector  # noqa: F811

        self._nodes: dict[UUID, NodeBase] = {}
        self._outgoing: dict[UUID, list[EdgeBase]] = defaultdict(list)
        self._incoming: dict[UUID, list[EdgeBase]] = defaultdict(list)
        self._collector: MetricsCollector | None = collector

    # --- Node operations ---

    def _record_query(
        self, operation: str, node_type: str, status: str, duration: float
    ) -> None:
        if self._collector:
            self._collector.record_neo4j_query(operation, node_type, status, duration)

    async def add_node(self, node: NodeBase) -> NodeBase:
        t0 = time.monotonic()
        ntype = getattr(node, "node_type", "unknown")
        try:
            if node.id in self._nodes:
                raise ValueError(f"Node with ID {node.id} already exists")
            self._nodes[node.id] = node
            self._record_query("add_node", str(ntype), "success", time.monotonic() - t0)
            return node
        except Exception:
            self._record_query("add_node", str(ntype), "error", time.monotonic() - t0)
            raise

    async def get_node(self, node_id: UUID) -> NodeBase | None:
        t0 = time.monotonic()
        result = self._nodes.get(node_id)
        ntype = str(getattr(result, "node_type", "unknown")) if result else "unknown"
        self._record_query("get_node", ntype, "success", time.monotonic() - t0)
        return result

    async def update_node(self, node_id: UUID, updates: dict) -> NodeBase:
        t0 = time.monotonic()
        node = self._nodes.get(node_id)
        if node is None:
            self._record_query("update_node", "unknown", "error", time.monotonic() - t0)
            raise KeyError(f"Node {node_id} not found")
        if hasattr(node, "updated_at") and "updated_at" not in updates:
            updates["updated_at"] = datetime.now(UTC)
        updated = node.model_copy(update=updates)
        self._nodes[node_id] = updated
        ntype = str(getattr(updated, "node_type", "unknown"))
        self._record_query("update_node", ntype, "success", time.monotonic() - t0)
        return updated

    async def delete_node(self, node_id: UUID) -> bool:
        t0 = time.monotonic()
        if node_id not in self._nodes:
            self._record_query("delete_node", "unknown", "error", time.monotonic() - t0)
            return False
        ntype = str(getattr(self._nodes[node_id], "node_type", "unknown"))
        del self._nodes[node_id]
        # Remove all edges connected to this node
        for edge in list(self._outgoing.get(node_id, [])):
            self._incoming[edge.target_id] = [
                e for e in self._incoming[edge.target_id] if e.source_id != node_id
            ]
        for edge in list(self._incoming.get(node_id, [])):
            self._outgoing[edge.source_id] = [
                e for e in self._outgoing[edge.source_id] if e.target_id != node_id
            ]
        self._outgoing.pop(node_id, None)
        self._incoming.pop(node_id, None)
        self._record_query("delete_node", ntype, "success", time.monotonic() - t0)
        return True

    async def list_nodes(
        self,
        node_type: NodeType | None = None,
        filters: dict | None = None,
    ) -> list[NodeBase]:
        t0 = time.monotonic()
        results = list(self._nodes.values())
        if node_type is not None:
            results = [n for n in results if n.node_type == node_type]
        if filters:
            for key, value in filters.items():
                results = [
                    n for n in results if hasattr(n, key) and getattr(n, key) == value
                ]
        ntype = str(node_type) if node_type else "all"
        self._record_query("list_nodes", ntype, "success", time.monotonic() - t0)
        return results

    # --- Edge operations ---

    async def add_edge(self, edge: EdgeBase) -> EdgeBase:
        t0 = time.monotonic()
        try:
            if edge.source_id not in self._nodes:
                raise ValueError(f"Source node {edge.source_id} does not exist")
            if edge.target_id not in self._nodes:
                raise ValueError(f"Target node {edge.target_id} does not exist")
            self._outgoing[edge.source_id].append(edge)
            self._incoming[edge.target_id].append(edge)
            self._record_query(
                "add_edge", str(edge.edge_type), "success", time.monotonic() - t0
            )
            return edge
        except Exception:
            self._record_query(
                "add_edge", str(edge.edge_type), "error", time.monotonic() - t0
            )
            raise

    async def get_edges(
        self,
        node_id: UUID,
        direction: str = "outgoing",
        edge_type: EdgeType | None = None,
    ) -> list[EdgeBase]:
        edges: list[EdgeBase] = []
        if direction in ("outgoing", "both"):
            edges.extend(self._outgoing.get(node_id, []))
        if direction in ("incoming", "both"):
            edges.extend(self._incoming.get(node_id, []))
        if edge_type is not None:
            edges = [e for e in edges if e.edge_type == edge_type]
        return edges

    async def remove_edge(
        self, source_id: UUID, target_id: UUID, edge_type: EdgeType
    ) -> bool:
        original_out = self._outgoing.get(source_id, [])
        new_out = [
            e
            for e in original_out
            if not (e.target_id == target_id and e.edge_type == edge_type)
        ]
        if len(new_out) == len(original_out):
            return False
        self._outgoing[source_id] = new_out
        self._incoming[target_id] = [
            e
            for e in self._incoming.get(target_id, [])
            if not (e.source_id == source_id and e.edge_type == edge_type)
        ]
        return True

    # --- Traversal queries ---

    async def get_neighbors(
        self,
        node_id: UUID,
        edge_type: EdgeType | None = None,
        direction: str = "outgoing",
    ) -> list[NodeBase]:
        edges = await self.get_edges(node_id, direction=direction, edge_type=edge_type)
        neighbor_ids: list[UUID] = []
        for edge in edges:
            nid = edge.target_id if edge.source_id == node_id else edge.source_id
            if nid not in neighbor_ids:
                neighbor_ids.append(nid)
        return [self._nodes[nid] for nid in neighbor_ids if nid in self._nodes]

    async def get_subgraph(
        self,
        root_id: UUID,
        depth: int = 2,
        edge_types: list[EdgeType] | None = None,
    ) -> SubGraph:
        if root_id not in self._nodes:
            raise KeyError(f"Root node {root_id} not found")

        visited_nodes: dict[UUID, NodeBase] = {}
        collected_edges: list[EdgeBase] = []
        queue: deque[tuple[UUID, int]] = deque([(root_id, 0)])
        visited_nodes[root_id] = self._nodes[root_id]

        while queue:
            current_id, current_depth = queue.popleft()
            if current_depth >= depth:
                continue

            for edge in self._outgoing.get(current_id, []):
                if edge_types and edge.edge_type not in edge_types:
                    continue
                collected_edges.append(edge)
                if edge.target_id not in visited_nodes:
                    visited_nodes[edge.target_id] = self._nodes[edge.target_id]
                    queue.append((edge.target_id, current_depth + 1))

        return SubGraph(
            nodes=list(visited_nodes.values()),
            edges=collected_edges,
            root_id=root_id,
            depth=depth,
        )

    async def traverse(
        self,
        root_id: UUID,
        edge_types: list[EdgeType],
        max_depth: int = 5,
    ) -> list[list[UUID]]:
        if root_id not in self._nodes:
            raise KeyError(f"Root node {root_id} not found")

        paths: list[list[UUID]] = []
        stack: list[tuple[UUID, list[UUID]]] = [(root_id, [root_id])]

        while stack:
            current_id, current_path = stack.pop()
            if len(current_path) - 1 >= max_depth:
                paths.append(current_path)
                continue

            has_children = False
            for edge in self._outgoing.get(current_id, []):
                if edge.edge_type not in edge_types:
                    continue
                if edge.target_id in current_path:
                    continue  # Avoid cycles
                has_children = True
                stack.append((edge.target_id, current_path + [edge.target_id]))

            if not has_children and len(current_path) > 1:
                paths.append(current_path)

        return paths
