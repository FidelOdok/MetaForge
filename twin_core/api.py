"""Twin API — unified facade for all Digital Twin operations.

Composes GraphEngine, VersionEngine, and ConstraintEngine into a single
entry point for agents, the orchestrator, and the gateway.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
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
from twin_core.models.datasheet import Datasheet
from twin_core.models.enums import EdgeType, NodeType, WorkProductType
from twin_core.models.relationship import SubGraph
from twin_core.models.version import Version, VersionDiff
from twin_core.models.work_product import WorkProduct
from twin_core.versioning.branch import InMemoryVersionEngine, VersionEngine


@dataclass
class OrphanReport:
    """Result of ``TwinAPI.find_orphans()`` (MET-429).

    Each list holds node UUIDs of the matching orphan category. A node
    is considered orphaned when **no edges** (incoming or outgoing)
    connect it to the rest of the graph — i.e. it is unreachable from
    its parent work product.
    """

    orphan_constraints: list[UUID] = field(default_factory=list)
    orphan_bom_items: list[UUID] = field(default_factory=list)
    orphan_design_elements: list[UUID] = field(default_factory=list)
    orphan_components: list[UUID] = field(default_factory=list)

    @property
    def total(self) -> int:
        return (
            len(self.orphan_constraints)
            + len(self.orphan_bom_items)
            + len(self.orphan_design_elements)
            + len(self.orphan_components)
        )

    @property
    def is_clean(self) -> bool:
        return self.total == 0


class OrphanWouldBeCreatedError(ValueError):
    """Raised by ``delete_work_product`` when the delete would orphan dependents (MET-438).

    Default-deny: callers must opt into ``cascade=True`` to remove
    dependent BOMItem / Constraint / DesignElement / Component nodes
    that would lose their last edge to the deleted work product.
    """

    def __init__(self, work_product_id: UUID, orphans: OrphanReport) -> None:
        self.work_product_id = work_product_id
        self.orphans = orphans
        super().__init__(
            f"Deleting WorkProduct {work_product_id} would orphan "
            f"{orphans.total} dependent node(s): "
            f"constraints={len(orphans.orphan_constraints)}, "
            f"bom_items={len(orphans.orphan_bom_items)}, "
            f"design_elements={len(orphans.orphan_design_elements)}, "
            f"components={len(orphans.orphan_components)}. "
            f"Pass cascade=True to remove the dependents along with the work product."
        )


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
    async def delete_work_product(
        self,
        work_product_id: UUID,
        branch: str = "main",
        cascade: bool = False,
    ) -> bool:
        """Delete a WorkProduct; opt-in cascade removes orphaned dependents.

        Default (``cascade=False``) raises :class:`OrphanWouldBeCreatedError`
        when any Constraint / BOMItem / DesignElement / Component would
        lose its last edge to the rest of the graph. ``cascade=True``
        deletes those dependents along with the WorkProduct.
        """
        ...

    @abstractmethod
    async def list_work_products(
        self,
        branch: str = "main",
        domain: str | None = None,
        work_product_type: WorkProductType | None = None,
        project_id: UUID | None = None,
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

    # --- Datasheets (MET-430) ---

    @abstractmethod
    async def ingest_datasheet(self, datasheet: Datasheet) -> Datasheet:
        """Ingest a datasheet idempotently by ``file_hash``.

        Behaviour:
        - If a ``Datasheet`` with the same ``file_hash`` already exists,
          return the existing node unchanged (no-op).
        - Otherwise, persist the node. When a prior revision of the
          same MPN exists, automatically link the new node to it with
          a ``SUPERSEDES`` edge (new → old).
        """
        ...

    @abstractmethod
    async def find_datasheets_by_mpn(self, mpn: str) -> list[Datasheet]:
        """All ingested datasheets for an MPN, every revision."""
        ...

    @abstractmethod
    async def get_current_datasheet(self, mpn: str) -> Datasheet | None:
        """The "current" revision for an MPN.

        Defined as the datasheet that is **not** superseded by any
        other datasheet (i.e. has no incoming ``SUPERSEDES`` edge).
        Returns ``None`` when no datasheet exists for the MPN.
        """
        ...

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

    # --- Subsystem accessors ---

    @property
    @abstractmethod
    def constraints(self) -> ConstraintEngine:
        """The live constraint engine.

        Exposed so MCP / orchestrator / gateway bootstrap can wire the
        engine into ``ToolRegistry`` without reaching into private state.
        """
        ...

    # --- Lifecycle ---

    @abstractmethod
    async def aclose(self) -> None:
        """Release any backing-store resources (Neo4j driver, sessions).

        Idempotent — calling more than once is a no-op. Callers that
        bootstrap a Twin (MCP entrypoint, gateway lifespan, tests) must
        await this in a ``finally`` block to avoid dangling drivers.
        """
        ...

    # --- Graph hygiene ---

    @abstractmethod
    async def find_orphans(self) -> OrphanReport:
        """Find dependent nodes with zero edges (MET-429).

        Constraint / BOMItem / DesignElement / Component nodes are
        "dependent" — they should always be reachable from some parent
        work product. When ``delete_work_product`` removes a parent,
        the dependent's edges are pruned but the node itself remains;
        this scan surfaces those leftovers.

        Returns an :class:`OrphanReport` with one list per node type.
        """
        ...


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
        collector: MetricsCollector | None = None,
    ) -> None:
        self._graph = graph
        self._version = version
        self._constraints = constraints
        # MET-439: optional metrics collector for the orphan gauge. When
        # None, metric emission is a no-op so unit tests don't need a
        # full OTel stack.
        self._collector = collector

    @property
    def constraints(self) -> ConstraintEngine:
        return self._constraints

    async def aclose(self) -> None:
        close = getattr(self._graph, "close", None)
        if close is not None and callable(close):
            await close()

    async def find_orphans(self) -> OrphanReport:
        report = OrphanReport()
        # Match each dependent node type to the field on OrphanReport
        # and to the metric label used by ``twin_orphans``. Adding a
        # new dependent type is one entry here, not a refactor.
        dependents = (
            (NodeType.CONSTRAINT, report.orphan_constraints, "constraint"),
            (NodeType.BOM_ITEM, report.orphan_bom_items, "bom_item"),
            (NodeType.DESIGN_ELEMENT, report.orphan_design_elements, "design_element"),
            (NodeType.COMPONENT, report.orphan_components, "component"),
        )
        for node_type, bucket, _kind in dependents:
            nodes = await self._graph.list_nodes(node_type=node_type)
            for node in nodes:
                outgoing = await self._graph.get_edges(node.id, direction="outgoing")
                incoming = await self._graph.get_edges(node.id, direction="incoming")
                if not outgoing and not incoming:
                    bucket.append(node.id)
        # MET-439: surface the per-kind counts to Prometheus so a
        # regression that re-introduces orphans paged automatically.
        if self._collector is not None:
            for _node_type, bucket, kind in dependents:
                self._collector.set_twin_orphans(kind, len(bucket))
        return report

    @classmethod
    def create(cls) -> InMemoryTwinAPI:
        """Convenience factory that wires up all in-memory subsystems."""
        graph = InMemoryGraphEngine()
        version = InMemoryVersionEngine(graph)
        constraints = InMemoryConstraintEngine(graph)
        return cls(graph=graph, version=version, constraints=constraints)

    @classmethod
    def create_with_collector(cls, collector: MetricsCollector | None = None) -> InMemoryTwinAPI:
        """Factory that passes a MetricsCollector to graph, constraint engines, and the TwinAPI."""

        graph = InMemoryGraphEngine(collector=collector)
        version = InMemoryVersionEngine(graph)
        constraints = InMemoryConstraintEngine(graph, collector=collector)
        return cls(graph=graph, version=version, constraints=constraints, collector=collector)

    @classmethod
    async def create_from_env(cls, collector: MetricsCollector | None = None) -> InMemoryTwinAPI:
        """Factory that selects the graph backend from environment variables.

        Automatically detects Neo4j when ``NEO4J_URI`` is set (as configured
        in docker-compose.yml).  Falls back to ``METAFORGE_GRAPH_BACKEND``
        / ``METAFORGE_NEO4J_*`` for explicit override.

        Environment variables (checked in order):
        - ``NEO4J_URI`` / ``METAFORGE_NEO4J_URI`` (default: ``bolt://localhost:7687``)
        - ``NEO4J_USER`` / ``METAFORGE_NEO4J_USER`` (default: ``neo4j``)
        - ``NEO4J_PASSWORD`` / ``METAFORGE_NEO4J_PASSWORD`` (default: ``password``)
        - ``METAFORGE_GRAPH_BACKEND`` — set to ``"neo4j"`` to force Neo4j even
          without ``NEO4J_URI``.
        """
        import structlog

        _logger = structlog.get_logger(__name__)

        neo4j_uri = os.environ.get("NEO4J_URI") or os.environ.get("METAFORGE_NEO4J_URI")
        backend = os.environ.get("METAFORGE_GRAPH_BACKEND", "memory").lower()

        use_neo4j = neo4j_uri is not None or backend == "neo4j"

        if use_neo4j:
            from twin_core.neo4j_graph_engine import Neo4jGraphEngine

            uri = neo4j_uri or "bolt://localhost:7687"
            user = os.environ.get("NEO4J_USER") or os.environ.get("METAFORGE_NEO4J_USER", "neo4j")
            password = os.environ.get("NEO4J_PASSWORD") or os.environ.get(
                "METAFORGE_NEO4J_PASSWORD", "password"
            )
            graph: GraphEngine = Neo4jGraphEngine(
                uri=uri,
                user=user,
                password=password,
            )
            await graph.connect()  # type: ignore[attr-defined]
            _logger.info("twin_api_neo4j_connected", uri=uri)
        else:
            graph = InMemoryGraphEngine(collector=collector)
            _logger.info("twin_api_using_in_memory_backend")

        version = InMemoryVersionEngine(graph)
        constraints = InMemoryConstraintEngine(graph, collector=collector)
        return cls(graph=graph, version=version, constraints=constraints, collector=collector)

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

    async def delete_work_product(
        self,
        work_product_id: UUID,
        branch: str = "main",
        cascade: bool = False,
    ) -> bool:
        # Identify dependents whose only edge points to the work_product
        # being deleted — they would orphan as a side effect.
        would_orphan = await self._dependents_that_would_orphan(work_product_id)

        if would_orphan.total > 0 and not cascade:
            raise OrphanWouldBeCreatedError(work_product_id, would_orphan)

        if cascade:
            for dep_id in (
                would_orphan.orphan_constraints
                + would_orphan.orphan_bom_items
                + would_orphan.orphan_design_elements
                + would_orphan.orphan_components
            ):
                await self._graph.delete_node(dep_id)

        return await self._graph.delete_node(work_product_id)

    async def _dependents_that_would_orphan(self, work_product_id: UUID) -> OrphanReport:
        """Find dependents whose *only* neighbour is the target work product.

        Returns an :class:`OrphanReport` listing the IDs by category.
        A dependent with edges to other nodes is NOT included — its
        connection survives the delete.
        """
        report = OrphanReport()
        candidate_types = (
            (NodeType.CONSTRAINT, report.orphan_constraints),
            (NodeType.BOM_ITEM, report.orphan_bom_items),
            (NodeType.DESIGN_ELEMENT, report.orphan_design_elements),
            (NodeType.COMPONENT, report.orphan_components),
        )
        for node_type, bucket in candidate_types:
            nodes = await self._graph.list_nodes(node_type=node_type)
            for node in nodes:
                outgoing = await self._graph.get_edges(node.id, direction="outgoing")
                incoming = await self._graph.get_edges(node.id, direction="incoming")
                edges = outgoing + incoming
                if not edges:
                    continue  # already orphan — unrelated to this delete
                touches_target = any(
                    e.source_id == work_product_id or e.target_id == work_product_id for e in edges
                )
                other_endpoints = {
                    e.source_id if e.source_id != node.id else e.target_id for e in edges
                }
                # Would orphan if removing the work_product clears every
                # neighbour — i.e. the only neighbour is the target.
                if touches_target and other_endpoints == {work_product_id}:
                    bucket.append(node.id)
        return report

    async def list_work_products(
        self,
        branch: str = "main",
        domain: str | None = None,
        work_product_type: WorkProductType | None = None,
        project_id: UUID | None = None,
    ) -> list[WorkProduct]:
        filters: dict[str, Any] = {}
        if domain is not None:
            filters["domain"] = domain
        if work_product_type is not None:
            filters["type"] = work_product_type
        # MET-428: tenant scoping. The underlying graph engine's filter
        # already does equality match on any attribute, so forwarding
        # ``project_id`` here is a one-line plumb-through.
        if project_id is not None:
            filters["project_id"] = project_id
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

    # --- Datasheets (MET-430) ---

    async def ingest_datasheet(self, datasheet: Datasheet) -> Datasheet:
        # Idempotency: same file_hash → return the existing node.
        existing = await self._graph.list_nodes(
            node_type=NodeType.DATASHEET, filters={"file_hash": datasheet.file_hash}
        )
        if existing:
            return existing[0]  # type: ignore[return-value]

        # Capture the prior current datasheet for this MPN *before* the
        # new node is inserted — otherwise it becomes its own ancestor.
        prior = await self.get_current_datasheet(datasheet.mpn)

        result = await self._graph.add_node(datasheet)

        if prior is not None and prior.id != datasheet.id:
            # SUPERSEDES points from the new revision to the old one.
            await self._graph.add_edge(
                EdgeBase(
                    source_id=datasheet.id,
                    target_id=prior.id,
                    edge_type=EdgeType.SUPERSEDES,
                )
            )

        # MET-430: link the datasheet to every Component that shares
        # its MPN. Auto-creation of the Component when none exists is
        # intentionally **not** done here — that would silently inject
        # nodes the user didn't author. Once a Component exists for
        # the MPN (via the supply chain agent or manual entry), the
        # next datasheet ingest connects them.
        components = await self._graph.list_nodes(
            node_type=NodeType.COMPONENT, filters={"part_number": datasheet.mpn}
        )
        for component in components:
            await self._graph.add_edge(
                EdgeBase(
                    source_id=datasheet.id,
                    target_id=component.id,
                    edge_type=EdgeType.DESCRIBES,
                )
            )
        return result  # type: ignore[return-value]

    async def find_datasheets_by_mpn(self, mpn: str) -> list[Datasheet]:
        nodes = await self._graph.list_nodes(node_type=NodeType.DATASHEET, filters={"mpn": mpn})
        return nodes  # type: ignore[return-value]

    async def get_current_datasheet(self, mpn: str) -> Datasheet | None:
        candidates = await self.find_datasheets_by_mpn(mpn)
        for ds in candidates:
            # "Current" = no other datasheet supersedes this one (no
            # incoming SUPERSEDES edge).
            incoming = await self._graph.get_edges(
                ds.id, direction="incoming", edge_type=EdgeType.SUPERSEDES
            )
            if not incoming:
                return ds
        return None

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
        if hasattr(self._graph, "query_cypher"):
            return await self._graph.query_cypher(query, params)  # type: ignore[attr-defined]
        raise NotImplementedError(
            "Cypher queries require the Neo4j backend. "
            "Set NEO4J_URI to enable, or use get_subgraph() / list_work_products()."
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
