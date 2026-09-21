"""Twin API — unified facade for all Digital Twin operations.

Composes GraphEngine, VersionEngine, and ConstraintEngine into a single
entry point for agents, the orchestrator, and the gateway.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

if TYPE_CHECKING:
    from observability.metrics import MetricsCollector

from twin_core.constraint_engine.models import ConstraintEvaluationResult
from twin_core.constraint_engine.validator import ConstraintEngine, InMemoryConstraintEngine
from twin_core.graph_engine import GraphEngine, InMemoryGraphEngine
from twin_core.models.base import EdgeBase
from twin_core.models.baseline import Baseline
from twin_core.models.bom_item import BOMItem
from twin_core.models.component import Component
from twin_core.models.constraint import Constraint
from twin_core.models.datasheet import Datasheet
from twin_core.models.engineering_change_transaction import EngineeringChangeTransaction
from twin_core.models.engineering_entity import EngineeringEntity
from twin_core.models.enums import EdgeType, NodeType, WorkProductType
from twin_core.models.relationship import SubGraph
from twin_core.models.revision_snapshot import RevisionSnapshot
from twin_core.models.version import Version, VersionDiff
from twin_core.models.work_product import WorkProduct
from twin_core.versioning.branch import InMemoryVersionEngine, VersionEngine
from twin_core.versioning.git_backend import GitVersionEngine

DEFAULT_NEO4J_CONNECT_ATTEMPTS = 5
"""Connect attempts before falling back (MET-710).

A live incident: the gateway and Neo4j restarted together, the gateway resolved
``neo4j`` a moment before its DNS entry existed, and it ran on the in-memory
twin for 43 hours — ignoring 623 persisted nodes, with ``/health`` still
reporting healthy. The proof that a retry is the right fix is in the same boot
log: the *consolidation insight store*, which connects to the same URI slightly
later in the lifespan, succeeded. The name was resolvable seconds afterwards.

Five attempts with the backoff below span ~7.5s, which covers a container-DNS
race without meaningfully delaying a boot where Neo4j is genuinely absent."""

_NEO4J_CONNECT_BACKOFF = (0.5, 1.0, 2.0, 4.0)


def neo4j_connect_attempts() -> int:
    """Configured connect attempts; ``METAFORGE_NEO4J_CONNECT_ATTEMPTS``."""
    raw = os.environ.get("METAFORGE_NEO4J_CONNECT_ATTEMPTS", "").strip()
    if not raw:
        return DEFAULT_NEO4J_CONNECT_ATTEMPTS
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_NEO4J_CONNECT_ATTEMPTS


async def _connect_with_retry(
    graph: Any,
    uri: str,
    logger: Any,
    *,
    attempts: int | None = None,
    sleep: Any = None,
) -> None:
    """Connect to Neo4j, retrying transient failures. Re-raises the last error.

    The caller decides what a final failure means (``create_from_env``'s
    contract is to propagate, and the gateway then either fails fast or falls
    back per ``METAFORGE_REQUIRE_NEO4J``) — this only removes the case where a
    *momentary* DNS or startup race is treated as a permanent absence.
    """
    import asyncio

    total = attempts if attempts is not None else neo4j_connect_attempts()
    delay = sleep if sleep is not None else asyncio.sleep
    last: Exception | None = None
    for attempt in range(total):
        try:
            await graph.connect()
            if attempt:
                logger.info("neo4j_connect_recovered", uri=uri, attempt=attempt + 1)
            return
        except Exception as exc:  # noqa: BLE001 — retried, then re-raised below
            last = exc
            if attempt == total - 1:
                break
            wait = _NEO4J_CONNECT_BACKOFF[min(attempt, len(_NEO4J_CONNECT_BACKOFF) - 1)]
            logger.warning(
                "neo4j_connect_retrying",
                uri=uri,
                attempt=attempt + 1,
                of=total,
                retry_in_s=wait,
                error=str(exc),
            )
            await delay(wait)
    assert last is not None
    logger.error("neo4j_connect_exhausted", uri=uri, attempts=total, error=str(last))
    raise last


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


class RevisionConflictError(ValueError):
    """Raised by ``update_constraint``/``update_engineering_entity`` (FORGE-50,
    Phase 2 epic FORGE-35) when a caller-supplied ``expected_revision`` no
    longer matches the node's current ``revision``.

    Optimistic concurrency, not locking: the caller read the node at some
    revision, computed a change, and is now asserting nothing else committed
    in between. A mismatch means it did -- the caller (typically
    ``TransactionEngine.commit``) must re-read the current state and decide
    whether to recompute or surface a conflict, never blindly retry with the
    same payload.
    """

    def __init__(self, node_id: UUID, expected: int, actual: int) -> None:
        self.node_id = node_id
        self.expected_revision = expected
        self.actual_revision = actual
        super().__init__(
            f"Revision conflict on {node_id}: expected revision {expected}, "
            f"found {actual}. The node changed since it was last read."
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
    async def update_constraint(
        self,
        constraint_id: UUID,
        updates: dict[str, Any],
        *,
        expected_revision: int | None = None,
    ) -> Constraint:
        """Apply ``updates`` and increment ``revision`` by 1 (FORGE-50).

        When ``expected_revision`` is given, raises
        :class:`RevisionConflictError` (no write applied) if the
        constraint's current revision doesn't match -- the optimistic-
        concurrency primitive ``TransactionEngine.commit`` builds on.
        """
        ...

    @abstractmethod
    async def get_constraint_revision(
        self, constraint_id: UUID, revision: int
    ) -> Constraint | None:
        """Return the Constraint as it stood at ``revision`` (FORGE-51).

        Returns the live node when ``revision`` matches its current
        revision, else reconstructs it from the ``RevisionSnapshot``
        recorded just before the update that moved it past that revision.
        ``None`` when the entity or that specific revision doesn't exist.
        """
        ...

    @abstractmethod
    async def evaluate_constraints(self, branch: str = "main") -> ConstraintEvaluationResult: ...

    @abstractmethod
    async def list_constraints(self, project_id: UUID | None = None) -> list[Constraint]:
        """List Constraint nodes, optionally scoped to a project.

        FORGE-45: the resolution side of the Engineering Intent & Requirements
        Harness's parent_refs hierarchy -- a caller names a parent by exact
        ``name``, matched client-side against this list (see
        ``api_gateway/twin/_ref_resolver.py``), the same way
        ``list_work_products`` already backs the SUPERSEDES exact-name-match
        precedent.
        """
        ...

    # --- Engineering Entities (FORGE-44/45) ---

    @abstractmethod
    async def create_engineering_entity(self, entity: EngineeringEntity) -> EngineeringEntity: ...

    @abstractmethod
    async def get_engineering_entity(self, entity_id: UUID) -> EngineeringEntity | None: ...

    @abstractmethod
    async def update_engineering_entity(
        self,
        entity_id: UUID,
        updates: dict[str, Any],
        *,
        expected_revision: int | None = None,
    ) -> EngineeringEntity:
        """Apply ``updates`` and increment ``revision`` by 1 (FORGE-50).

        Same optimistic-concurrency contract as ``update_constraint``.
        """
        ...

    @abstractmethod
    async def get_engineering_entity_revision(
        self, entity_id: UUID, revision: int
    ) -> EngineeringEntity | None:
        """Return the EngineeringEntity as it stood at ``revision`` (FORGE-51).

        Same reconstruction contract as ``get_constraint_revision``.
        """
        ...

    @abstractmethod
    async def list_engineering_entities(
        self, project_id: UUID | None = None, entity_type: str | None = None
    ) -> list[EngineeringEntity]:
        """List EngineeringEntity nodes, optionally scoped to a project and/or
        ``entity_type`` (intent | stakeholder_need | objective | assumption |
        question | risk | verification_case | evidence)."""
        ...

    # --- Baselines (FORGE-51) ---

    @abstractmethod
    async def create_baseline(self, baseline: Baseline) -> Baseline:
        """Persist an already-built Baseline node.

        Callers should go through
        ``twin_core.transactions.baseline.create_baseline`` rather than
        calling this directly -- it's the piece that atomically bumps every
        included entity's authority to BASELINED alongside this write.
        """
        ...

    @abstractmethod
    async def get_baseline(self, baseline_id: UUID) -> Baseline | None: ...

    @abstractmethod
    async def list_baselines(self, project_id: UUID | None = None) -> list[Baseline]: ...

    # --- Engineering Change Transactions (FORGE-66) ---

    @abstractmethod
    async def create_ect(self, ect: EngineeringChangeTransaction) -> EngineeringChangeTransaction:
        """Persist a newly-proposed ECT.

        Callers should go through
        ``twin_core.transactions.ect.propose_change`` rather than calling
        this directly -- same "compositional wrapper, not a bare CRUD call"
        precedent as ``create_baseline``.
        """
        ...

    @abstractmethod
    async def get_ect(self, ect_id: UUID) -> EngineeringChangeTransaction | None: ...

    @abstractmethod
    async def update_ect(
        self, ect_id: UUID, updates: dict[str, Any]
    ) -> EngineeringChangeTransaction:
        """Apply ``updates`` to an ECT's mutable lifecycle fields (status,
        affected_objects, impact, approval_required, decided_by,
        decision_reason, committed_patch_result). Unlike ``update_constraint``/
        ``update_engineering_entity`` this does NOT bump a revision counter
        -- an ECT isn't a FORGE-50 controlled entity, it's the container
        that proposes changes TO them.
        """
        ...

    @abstractmethod
    async def list_ects(
        self, project_id: UUID | None = None, status: str | None = None
    ) -> list[EngineeringChangeTransaction]: ...

    # --- Components ---

    @abstractmethod
    async def add_component(self, component: Component) -> Component: ...

    @abstractmethod
    async def get_component(self, component_id: UUID) -> Component | None: ...

    @abstractmethod
    async def find_components(self, query: dict[str, Any]) -> list[Component]: ...

    @abstractmethod
    async def list_bom_items(self, project_id: UUID | None = None) -> list[BOMItem]:
        """List Bill-of-Materials line items, optionally scoped to a project."""
        ...

    @abstractmethod
    async def add_bom_item(self, item: BOMItem) -> BOMItem:
        """Persist one BOM line item (MET-436 follow-up).

        Mirrors :meth:`add_component` exactly — a plain graph-node write,
        no dedup. Callers that need a canonical singleton (e.g. "don't
        double-add the same MPN") do that check themselves before calling,
        the same way ``add_component`` leaves it to its caller.
        """
        ...

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

    @abstractmethod
    async def is_datasheet_stale(self, mpn: str, against: datetime) -> bool:
        """Return True when the current datasheet for ``mpn`` is newer than ``against``.

        ``against`` is typically the timestamp of a derived artifact
        (extracted property, BOM entry, constraint value) — when the
        upstream datasheet has been republished since, the derived
        artifact's source is stale and should be re-extracted.

        Returns False when no datasheet exists for the MPN or when the
        current revision predates ``against``.
        """
        ...

    @abstractmethod
    async def list_stale_datasheets(self, since: datetime) -> list[Datasheet]:
        """Every current datasheet whose ``published_at`` is after ``since``.

        Returns the head-of-chain datasheet per MPN (the same set
        ``get_current_datasheet`` walks per MPN) filtered to those
        published more recently than the cutoff. Datasheets with a
        null ``published_at`` are excluded — comparison is only
        meaningful when both sides have timestamps.
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
        direction: str = "outgoing",
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

    @property
    @abstractmethod
    def graph(self) -> GraphEngine:
        """The live graph engine.

        Exposed (MET-630) so gateway bootstrap can construct a
        ``GitRepoRegistry``/``GitVersionEngine`` against the same backing
        graph without reaching into private state.
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

    @property
    def graph(self) -> GraphEngine:
        return self._graph

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
        - ``METAFORGE_VERSION_BACKEND`` — set to ``"git"`` to back versioning
          with a real git repository (see ``GitVersionEngine``) instead of
          the default in-memory Version DAG. Requires
          ``METAFORGE_VERSION_GIT_ROOT`` to point at a writable directory.
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
            await _connect_with_retry(graph, uri, _logger)
            _logger.info("twin_api_neo4j_connected", uri=uri)
        else:
            graph = InMemoryGraphEngine(collector=collector)
            _logger.info("twin_api_using_in_memory_backend")

        version_backend = os.environ.get("METAFORGE_VERSION_BACKEND", "memory").lower()
        version: VersionEngine
        if version_backend == "git":
            git_root = os.environ.get("METAFORGE_VERSION_GIT_ROOT")
            if not git_root:
                raise ValueError(
                    "METAFORGE_VERSION_BACKEND=git requires METAFORGE_VERSION_GIT_ROOT "
                    "to point at a writable directory for the version repo"
                )
            version = GitVersionEngine(graph, git_root)
            _logger.info("twin_api_using_git_version_backend", repo_path=git_root)
        else:
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

    async def update_constraint(
        self,
        constraint_id: UUID,
        updates: dict[str, Any],
        *,
        expected_revision: int | None = None,
    ) -> Constraint:
        current = await self._graph.get_node(constraint_id)
        if current is None or not isinstance(current, Constraint):
            raise KeyError(f"Constraint {constraint_id} not found")
        if expected_revision is not None and current.revision != expected_revision:
            raise RevisionConflictError(constraint_id, expected_revision, current.revision)
        await self._graph.add_node(
            RevisionSnapshot(
                entity_id=constraint_id,
                entity_kind="constraint",
                revision=current.revision,
                data=current.model_dump(mode="json"),
            )
        )
        applied = dict(updates)
        applied["revision"] = current.revision + 1
        result = await self._graph.update_node(constraint_id, applied)
        return result  # type: ignore[return-value]

    async def get_constraint_revision(
        self, constraint_id: UUID, revision: int
    ) -> Constraint | None:
        current = await self.get_constraint(constraint_id)
        if current is not None and current.revision == revision:
            return current
        snapshots = await self._graph.list_nodes(
            node_type=NodeType.REVISION_SNAPSHOT,
            filters={"entity_id": constraint_id, "revision": revision},
        )
        if not snapshots:
            return None
        snapshot = snapshots[0]
        return Constraint.model_validate(snapshot.data)  # type: ignore[attr-defined]

    async def evaluate_constraints(self, branch: str = "main") -> ConstraintEvaluationResult:
        return await self._constraints.evaluate_all()

    async def list_constraints(self, project_id: UUID | None = None) -> list[Constraint]:
        filters: dict[str, Any] = {}
        if project_id is not None:
            filters["project_id"] = project_id
        nodes = await self._graph.list_nodes(
            node_type=NodeType.CONSTRAINT, filters=filters if filters else None
        )
        return nodes  # type: ignore[return-value]

    # --- Engineering Entities (FORGE-44/45) ---

    async def create_engineering_entity(self, entity: EngineeringEntity) -> EngineeringEntity:
        existing = await self._graph.get_node(entity.id)
        if existing is not None:
            raise ValueError(f"EngineeringEntity with ID {entity.id} already exists")
        result = await self._graph.add_node(entity)
        return result  # type: ignore[return-value]

    async def get_engineering_entity(self, entity_id: UUID) -> EngineeringEntity | None:
        node = await self._graph.get_node(entity_id)
        if node is not None and isinstance(node, EngineeringEntity):
            return node
        return None

    async def update_engineering_entity(
        self,
        entity_id: UUID,
        updates: dict[str, Any],
        *,
        expected_revision: int | None = None,
    ) -> EngineeringEntity:
        current = await self._graph.get_node(entity_id)
        if current is None or not isinstance(current, EngineeringEntity):
            raise KeyError(f"EngineeringEntity {entity_id} not found")
        if expected_revision is not None and current.revision != expected_revision:
            raise RevisionConflictError(entity_id, expected_revision, current.revision)
        await self._graph.add_node(
            RevisionSnapshot(
                entity_id=entity_id,
                entity_kind="engineering_entity",
                revision=current.revision,
                data=current.model_dump(mode="json"),
            )
        )
        applied = dict(updates)
        applied["revision"] = current.revision + 1
        result = await self._graph.update_node(entity_id, applied)
        return result  # type: ignore[return-value]

    async def get_engineering_entity_revision(
        self, entity_id: UUID, revision: int
    ) -> EngineeringEntity | None:
        current = await self.get_engineering_entity(entity_id)
        if current is not None and current.revision == revision:
            return current
        snapshots = await self._graph.list_nodes(
            node_type=NodeType.REVISION_SNAPSHOT,
            filters={"entity_id": entity_id, "revision": revision},
        )
        if not snapshots:
            return None
        snapshot = snapshots[0]
        return EngineeringEntity.model_validate(snapshot.data)  # type: ignore[attr-defined]

    async def list_engineering_entities(
        self, project_id: UUID | None = None, entity_type: str | None = None
    ) -> list[EngineeringEntity]:
        filters: dict[str, Any] = {}
        if project_id is not None:
            filters["project_id"] = project_id
        if entity_type is not None:
            filters["entity_type"] = entity_type
        nodes = await self._graph.list_nodes(
            node_type=NodeType.ENGINEERING_ENTITY, filters=filters if filters else None
        )
        return nodes  # type: ignore[return-value]

    # --- Baselines (FORGE-51) ---

    async def create_baseline(self, baseline: Baseline) -> Baseline:
        existing = await self._graph.get_node(baseline.id)
        if existing is not None:
            raise ValueError(f"Baseline with ID {baseline.id} already exists")
        result = await self._graph.add_node(baseline)
        return result  # type: ignore[return-value]

    async def get_baseline(self, baseline_id: UUID) -> Baseline | None:
        node = await self._graph.get_node(baseline_id)
        if node is not None and isinstance(node, Baseline):
            return node
        return None

    async def list_baselines(self, project_id: UUID | None = None) -> list[Baseline]:
        filters: dict[str, Any] = {}
        if project_id is not None:
            filters["project_id"] = project_id
        nodes = await self._graph.list_nodes(
            node_type=NodeType.BASELINE, filters=filters if filters else None
        )
        return nodes  # type: ignore[return-value]

    # --- Engineering Change Transactions (FORGE-66) ---

    async def create_ect(self, ect: EngineeringChangeTransaction) -> EngineeringChangeTransaction:
        existing = await self._graph.get_node(ect.id)
        if existing is not None:
            raise ValueError(f"EngineeringChangeTransaction with ID {ect.id} already exists")
        result = await self._graph.add_node(ect)
        return result  # type: ignore[return-value]

    async def get_ect(self, ect_id: UUID) -> EngineeringChangeTransaction | None:
        node = await self._graph.get_node(ect_id)
        if node is not None and isinstance(node, EngineeringChangeTransaction):
            return node
        return None

    async def update_ect(
        self, ect_id: UUID, updates: dict[str, Any]
    ) -> EngineeringChangeTransaction:
        result = await self._graph.update_node(ect_id, updates)
        return result  # type: ignore[return-value]

    async def list_ects(
        self, project_id: UUID | None = None, status: str | None = None
    ) -> list[EngineeringChangeTransaction]:
        filters: dict[str, Any] = {}
        if project_id is not None:
            filters["project_id"] = project_id
        if status is not None:
            filters["status"] = status
        nodes = await self._graph.list_nodes(
            node_type=NodeType.ENGINEERING_CHANGE_TRANSACTION,
            filters=filters if filters else None,
        )
        return nodes  # type: ignore[return-value]

    # --- Components ---

    async def list_bom_items(self, project_id: UUID | None = None) -> list[BOMItem]:
        filters: dict[str, Any] = {}
        if project_id is not None:
            filters["project_id"] = project_id
        nodes = await self._graph.list_nodes(
            node_type=NodeType.BOM_ITEM, filters=filters if filters else None
        )
        return nodes  # type: ignore[return-value]

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

    async def add_bom_item(self, item: BOMItem) -> BOMItem:
        result = await self._graph.add_node(item)
        return result  # type: ignore[return-value]

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

    async def is_datasheet_stale(self, mpn: str, against: datetime) -> bool:
        current = await self.get_current_datasheet(mpn)
        if current is None or current.published_at is None:
            return False
        return current.published_at > against

    async def list_stale_datasheets(self, since: datetime) -> list[Datasheet]:
        # Walk every MPN with at least one ingested datasheet and pull
        # the head of its supersedes chain. Cheaper than re-running
        # get_current_datasheet over candidates we've already seen.
        all_datasheets = await self._graph.list_nodes(node_type=NodeType.DATASHEET)
        seen_mpns: set[str] = set()
        stale: list[Datasheet] = []
        for ds in all_datasheets:
            mpn = getattr(ds, "mpn", None)
            if mpn is None or mpn in seen_mpns:
                continue
            seen_mpns.add(mpn)
            current = await self.get_current_datasheet(mpn)
            if current is None or current.published_at is None:
                continue
            if current.published_at > since:
                stale.append(current)
        return stale

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
        direction: str = "outgoing",
    ) -> SubGraph:
        return await self._graph.get_subgraph(
            root_id, depth=depth, edge_types=edge_types, direction=direction
        )

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
        try:
            head = await self._version.get_head(from_branch)
        except KeyError:
            return await self._version.create_branch(name)
        return await self._version.create_branch(name, from_version=head.id)

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
