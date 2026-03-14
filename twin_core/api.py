"""Twin API — unified facade for all Digital Twin operations.

Composes GraphEngine, VersionEngine, and ConstraintEngine into a single
entry point for agents, the orchestrator, and the gateway.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any
from uuid import UUID

if TYPE_CHECKING:
    from observability.metrics import MetricsCollector

from twin_core.constraint_engine.models import ConstraintEvaluationResult
from twin_core.constraint_engine.validator import ConstraintEngine, InMemoryConstraintEngine
from twin_core.graph_engine import GraphEngine, InMemoryGraphEngine
from twin_core.models.base import EdgeBase
from twin_core.models.component import Component
from twin_core.models.constraint import Constraint
from twin_core.models.enums import EdgeType, NodeType, WorkProductType
from twin_core.models.relationship import SubGraph
from twin_core.models.version import Version, VersionDiff
from twin_core.models.work_product import WorkProduct
from twin_core.versioning.branch import InMemoryVersionEngine, VersionEngine


class TwinAPI(ABC):
    """Abstract facade for all Digital Twin operations.

    Groups 22 methods across six categories:
    - Artifacts (5): create, get, update, delete, list
    - Constraints (3): create, get, evaluate
    - Components (3): add, get, find
    - Relationships (3): add_edge, get_edges, remove_edge
    - Queries (2): get_subgraph, query_cypher
    - Versioning (5): create_branch, commit, merge, diff, log
    """

    # --- Artifacts ---

    @abstractmethod
    async def create_work_product(
        self, work_product: WorkProduct, branch: str = "main"
    ) -> WorkProduct: ...

    @abstractmethod
    async def get_work_product(
        self, work_product_id: UUID, branch: str = "main"
    ) -> WorkProduct | None: ...

    @abstractmethod
    async def update_work_product(
        self, work_product_id: UUID, updates: dict[str, Any], branch: str = "main"
    ) -> WorkProduct: ...

    @abstractmethod
    async def delete_work_product(self, work_product_id: UUID, branch: str = "main") -> bool: ...

    @abstractmethod
    async def list_work_products(
        self,
        branch: str = "main",
        domain: str | None = None,
        work_product_type: WorkProductType | None = None,
    ) -> list[WorkProduct]: ...

    # --- Constraints ---

    @abstractmethod
    async def create_constraint(self, constraint: Constraint) -> Constraint: ...

    @abstractmethod
    async def get_constraint(self, constraint_id: UUID) -> Constraint | None: ...

    @abstractmethod
    async def evaluate_constraints(self, branch: str = "main") -> ConstraintEvaluationResult: ...

    # --- Components ---

    @abstractmethod
    async def add_component(self, component: Component) -> Component: ...

    @abstractmethod
    async def get_component(self, component_id: UUID) -> Component | None: ...

    @abstractmethod
    async def find_components(self, query: dict[str, Any]) -> list[Component]: ...

    # --- Relationships ---

    @abstractmethod
    async def add_edge(
        self,
        source_id: UUID,
        target_id: UUID,
        edge_type: EdgeType,
        metadata: dict[str, Any] | None = None,
    ) -> EdgeBase: ...

    @abstractmethod
    async def get_edges(
        self,
        node_id: UUID,
        direction: str = "outgoing",
        edge_type: EdgeType | None = None,
    ) -> list[EdgeBase]: ...

    @abstractmethod
    async def remove_edge(self, source_id: UUID, target_id: UUID, edge_type: EdgeType) -> bool: ...

    # --- Queries ---

    @abstractmethod
    async def get_subgraph(
        self,
        root_id: UUID,
        depth: int = 2,
        edge_types: list[EdgeType] | None = None,
    ) -> SubGraph: ...

    @abstractmethod
    async def query_cypher(
        self, query: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]: ...

    # --- Versioning ---

    @abstractmethod
    async def create_branch(self, name: str, from_branch: str = "main") -> str: ...

    @abstractmethod
    async def commit(self, branch: str, message: str, author: str) -> Version: ...

    @abstractmethod
    async def merge(self, source: str, target: str, message: str, author: str) -> Version: ...

    @abstractmethod
    async def diff(self, branch_a: str, branch_b: str) -> VersionDiff: ...

    @abstractmethod
    async def log(self, branch: str = "main", limit: int = 50) -> list[Version]: ...


class InMemoryTwinAPI(TwinAPI):
    """In-memory implementation of the Twin API facade.

    Composes InMemoryGraphEngine, InMemoryVersionEngine, and
    InMemoryConstraintEngine via dependency injection.
    """

    def __init__(
        self,
        graph: GraphEngine,
        version: VersionEngine,
        constraints: ConstraintEngine,
    ) -> None:
        self._graph = graph
        self._version = version
        self._constraints = constraints

    @classmethod
    def create(cls) -> InMemoryTwinAPI:
        """Convenience factory that wires up all in-memory subsystems."""
        graph = InMemoryGraphEngine()
        version = InMemoryVersionEngine(graph)
        constraints = InMemoryConstraintEngine(graph)
        return cls(graph=graph, version=version, constraints=constraints)

    @classmethod
    def create_with_collector(cls, collector: MetricsCollector | None = None) -> InMemoryTwinAPI:
        """Factory that passes a MetricsCollector to graph and constraint engines."""

        graph = InMemoryGraphEngine(collector=collector)
        version = InMemoryVersionEngine(graph)
        constraints = InMemoryConstraintEngine(graph, collector=collector)
        return cls(graph=graph, version=version, constraints=constraints)

    @classmethod
    async def create_from_env(cls) -> InMemoryTwinAPI:
        """Factory that selects the graph backend from environment variables.

        Reads ``METAFORGE_GRAPH_BACKEND`` (default: ``"memory"``).
        When set to ``"neo4j"``, also reads:
        - ``METAFORGE_NEO4J_URI`` (default: ``bolt://localhost:7687``)
        - ``METAFORGE_NEO4J_USER`` (default: ``neo4j``)
        - ``METAFORGE_NEO4J_PASSWORD`` (default: ``password``)
        """
        backend = os.environ.get("METAFORGE_GRAPH_BACKEND", "memory").lower()
        if backend == "neo4j":
            from twin_core.neo4j_graph_engine import Neo4jGraphEngine

            uri = os.environ.get("METAFORGE_NEO4J_URI", "bolt://localhost:7687")
            user = os.environ.get("METAFORGE_NEO4J_USER", "neo4j")
            password = os.environ.get("METAFORGE_NEO4J_PASSWORD", "password")
            graph: GraphEngine = Neo4jGraphEngine(
                uri=uri,
                user=user,
                password=password,
            )
            await graph.connect()  # type: ignore[attr-defined]
        else:
            graph = InMemoryGraphEngine()

        version = InMemoryVersionEngine(graph)
        constraints = InMemoryConstraintEngine(graph)
        return cls(graph=graph, version=version, constraints=constraints)

    # --- Artifacts ---

    async def create_work_product(
        self, work_product: WorkProduct, branch: str = "main"
    ) -> WorkProduct:
        result = await self._graph.add_node(work_product)
        return result  # type: ignore[return-value]

    async def get_work_product(
        self, work_product_id: UUID, branch: str = "main"
    ) -> WorkProduct | None:
        node = await self._graph.get_node(work_product_id)
        if node is not None and isinstance(node, WorkProduct):
            return node
        return None

    async def update_work_product(
        self, work_product_id: UUID, updates: dict[str, Any], branch: str = "main"
    ) -> WorkProduct:
        result = await self._graph.update_node(work_product_id, updates)
        return result  # type: ignore[return-value]

    async def delete_work_product(self, work_product_id: UUID, branch: str = "main") -> bool:
        return await self._graph.delete_node(work_product_id)

    async def list_work_products(
        self,
        branch: str = "main",
        domain: str | None = None,
        work_product_type: WorkProductType | None = None,
    ) -> list[WorkProduct]:
        filters: dict[str, Any] = {}
        if domain is not None:
            filters["domain"] = domain
        if work_product_type is not None:
            filters["type"] = work_product_type
        nodes = await self._graph.list_nodes(
            node_type=NodeType.WORK_PRODUCT, filters=filters if filters else None
        )
        return nodes  # type: ignore[return-value]

    # --- Constraints ---

    async def create_constraint(self, constraint: Constraint) -> Constraint:
        # Add constraint node without work_product bindings — caller uses add_edge separately
        existing = await self._graph.get_node(constraint.id)
        if existing is not None:
            raise ValueError(f"Constraint with ID {constraint.id} already exists")
        result = await self._graph.add_node(constraint)
        return result  # type: ignore[return-value]

    async def get_constraint(self, constraint_id: UUID) -> Constraint | None:
        return await self._constraints.get_constraint(constraint_id)

    async def evaluate_constraints(self, branch: str = "main") -> ConstraintEvaluationResult:
        return await self._constraints.evaluate_all()

    # --- Components ---

    async def add_component(self, component: Component) -> Component:
        result = await self._graph.add_node(component)
        return result  # type: ignore[return-value]

    async def get_component(self, component_id: UUID) -> Component | None:
        node = await self._graph.get_node(component_id)
        if node is not None and isinstance(node, Component):
            return node
        return None

    async def find_components(self, query: dict[str, Any]) -> list[Component]:
        nodes = await self._graph.list_nodes(node_type=NodeType.COMPONENT, filters=query)
        return nodes  # type: ignore[return-value]

    # --- Relationships ---

    async def add_edge(
        self,
        source_id: UUID,
        target_id: UUID,
        edge_type: EdgeType,
        metadata: dict[str, Any] | None = None,
    ) -> EdgeBase:
        edge = EdgeBase(
            source_id=source_id,
            target_id=target_id,
            edge_type=edge_type,
            metadata=metadata or {},
        )
        return await self._graph.add_edge(edge)

    async def get_edges(
        self,
        node_id: UUID,
        direction: str = "outgoing",
        edge_type: EdgeType | None = None,
    ) -> list[EdgeBase]:
        return await self._graph.get_edges(node_id, direction=direction, edge_type=edge_type)

    async def remove_edge(self, source_id: UUID, target_id: UUID, edge_type: EdgeType) -> bool:
        return await self._graph.remove_edge(source_id, target_id, edge_type)

    # --- Queries ---

    async def get_subgraph(
        self,
        root_id: UUID,
        depth: int = 2,
        edge_types: list[EdgeType] | None = None,
    ) -> SubGraph:
        return await self._graph.get_subgraph(root_id, depth=depth, edge_types=edge_types)

    async def query_cypher(
        self, query: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        raise NotImplementedError(
            "Cypher queries require the Neo4j backend (planned for v0.2+). "
            "Use get_subgraph() or list_work_products() for in-memory queries."
        )

    # --- Versioning ---

    async def create_branch(self, name: str, from_branch: str = "main") -> str:
        if from_branch in self._version._branches:  # type: ignore[attr-defined]
            head_id = self._version._branches[from_branch]  # type: ignore[attr-defined]
            return await self._version.create_branch(name, from_version=head_id)
        return await self._version.create_branch(name)

    async def commit(self, branch: str, message: str, author: str) -> Version:
        return await self._version.commit(branch, message, [], author)

    async def merge(self, source: str, target: str, message: str, author: str) -> Version:
        return await self._version.merge(source, target, message, author)

    async def diff(self, branch_a: str, branch_b: str) -> VersionDiff:
        head_a = await self._version.get_head(branch_a)
        head_b = await self._version.get_head(branch_b)
        return await self._version.diff(head_a.id, head_b.id)

    async def log(self, branch: str = "main", limit: int = 50) -> list[Version]:
        return await self._version.log(branch, limit)
