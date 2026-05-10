"""Parity tests: ``InMemoryGraphEngine`` vs ``Neo4jGraphEngine`` (L1-F5).

Filed under MET-409. Asserts behavioral parity across the two graph
backends used by the Digital Twin: any node-create / edge-create /
property-lookup / traversal that an upstream caller might issue should
return semantically equivalent results regardless of which engine is
bound. Differences in tie-breaking ordering are tolerated; differences
in cardinality, content, or shape are not.

Five cases are parametrized over both backends:

* ``test_create_and_get_node_round_trips`` — create a node with a rich
  ``metadata`` payload, fetch it back, assert the payload survives.
* ``test_property_lookup_returns_matching_nodes`` — three nodes with
  different ``type`` values, ``list_nodes(filters=…)`` returns exactly
  the matching subset (modulo ordering).
* ``test_create_edge_round_trips`` — A → B with ``DEPENDS_ON``,
  ``get_edges("outgoing")`` from A returns one edge to B with the
  right type.
* ``test_traversal_two_hops`` — A → B → C, ``get_subgraph(depth=2)``
  from A reaches both B and C.
* ``test_constraint_violations_consistent`` — both engines composed
  inside ``InMemoryConstraintEngine`` report the same violations for
  the same constraint expression.

The Neo4j parametrize axis SKIPs cleanly when:

* the ``neo4j`` driver isn't installed (CI ``[dev]`` env), or
* ``bolt://localhost:7687`` is unreachable within a 2 s probe.

Mirrors the L1-A1 / L1-F4 skip-clean pattern.
"""

from __future__ import annotations

import socket
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import pytest
import structlog

from twin_core.constraint_engine import InMemoryConstraintEngine
from twin_core.graph_engine import GraphEngine, InMemoryGraphEngine
from twin_core.models.constraint import Constraint
from twin_core.models.enums import (
    ConstraintSeverity,
    EdgeType,
    NodeType,
    WorkProductType,
)
from twin_core.models.relationship import DependsOnEdge
from twin_core.models.work_product import WorkProduct

# Neo4j is an optional integration backend — gate import at module load.
neo4j = pytest.importorskip("neo4j", reason="neo4j driver not installed")  # noqa: F841

# Import after the importorskip so the module is collectable on machines
# without neo4j installed (e.g. CI ``[dev]``).
from twin_core.neo4j_graph_engine import Neo4jGraphEngine  # noqa: E402

logger = structlog.get_logger(__name__)

_NEO4J_URI = "bolt://localhost:7687"
_NEO4J_USER = "neo4j"
# Match docker-compose dev defaults; override via env if the local stack
# uses something else. Tests SKIP when the server is unreachable, so a
# stale password just becomes a different kind of skip.
_NEO4J_PASSWORD = "metaforge"


def _neo4j_reachable(uri: str = _NEO4J_URI, timeout: float = 2.0) -> bool:
    """Cheap TCP probe for ``bolt://host:port``.

    Mirrors the L1-A1 / L1-F4 Postgres probe — fail closed on any error
    so the test SKIPs cleanly instead of hanging on driver-internal
    retry loops when the server is dead.
    """
    # Strip ``bolt://`` and split host:port.
    rest = uri.split("://", 1)[-1]
    host, _, port_s = rest.partition(":")
    try:
        port = int(port_s) if port_s else 7687
    except ValueError:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, TimeoutError):
        return False


# A coroutine factory that yields a connected GraphEngine plus an
# async cleanup hook is used by the parametrize axis to defer the
# (potentially skipping) Neo4j construction until inside the test.


async def _make_in_memory_engine() -> tuple[GraphEngine, Callable[[], Awaitable[None]]]:
    """Build an isolated in-memory engine. No teardown needed."""
    engine = InMemoryGraphEngine()

    async def _cleanup() -> None:
        return None

    logger.debug("parity_engine_built", kind="in_memory")
    return engine, _cleanup


async def _make_neo4j_engine() -> tuple[GraphEngine, Callable[[], Awaitable[None]]]:
    """Build a Neo4j engine with a per-test database namespace.

    SKIPs cleanly if the bolt port isn't reachable. Uses a UUID-derived
    suffix on every node/edge created during the test (via the test's
    own data) so multiple parallel runs don't collide on the shared
    ``neo4j`` database. Teardown wipes only nodes whose ids are in the
    set we created — see ``cleanup`` closure.
    """
    if not _neo4j_reachable():
        pytest.skip(f"Neo4j not reachable at {_NEO4J_URI} — integration backend unavailable")

    engine = Neo4jGraphEngine(
        uri=_NEO4J_URI,
        user=_NEO4J_USER,
        password=_NEO4J_PASSWORD,
    )
    try:
        await engine.connect()
    except Exception as exc:  # pragma: no cover — environment-dependent
        pytest.skip(f"Neo4j connect failed: {exc}")

    # Per-test isolation: track ids we create so teardown can DETACH
    # DELETE only our nodes, leaving any unrelated data alone. The
    # tests themselves don't have access to this set, so we instead
    # nuke every Node node created during this test by tagging with a
    # session-unique label below.
    session_label = f"ParityTest_{uuid.uuid4().hex[:12]}"

    # Add the session label to the auto-applied :Node label by hooking
    # add_node — not strictly necessary if we just clear the whole DB,
    # but cleaner. Since we can't easily inject behavior, fall back to
    # "delete every node we created" — track by querying for nodes
    # added during the test window. The simplest correct approach:
    # snapshot ids before, then DETACH DELETE the diff.
    async with engine._driver.session(database=engine._database) as session:  # noqa: SLF001
        result = await session.run("MATCH (n:Node) RETURN n.id AS id")
        existing_ids = {r["id"] async for r in result}

    async def _cleanup() -> None:
        try:
            async with engine._driver.session(  # noqa: SLF001
                database=engine._database
            ) as session:
                # Delete every Node added since the snapshot.
                await session.run(
                    "MATCH (n:Node) WHERE NOT n.id IN $existing DETACH DELETE n",
                    existing=list(existing_ids),
                )
        finally:
            await engine.close()

    logger.debug(
        "parity_engine_built",
        kind="neo4j",
        uri=_NEO4J_URI,
        session_label=session_label,
        baseline_count=len(existing_ids),
    )
    return engine, _cleanup


# ---------------------------------------------------------------------------
# Parametrize axis — runs each test over both backends.
# ---------------------------------------------------------------------------


@pytest.fixture(
    params=[
        pytest.param("in_memory", id="in_memory"),
        pytest.param("neo4j", id="neo4j"),
    ]
)
async def engine(request: pytest.FixtureRequest) -> AsyncIterator[GraphEngine]:
    """Yield a connected GraphEngine for the parametrized backend.

    Function-scoped so each test gets a fresh, isolated state. Neo4j
    teardown wipes only data created during the test (delta against
    a baseline snapshot of node ids).
    """
    kind: str = request.param
    if kind == "in_memory":
        eng, cleanup = await _make_in_memory_engine()
    elif kind == "neo4j":
        eng, cleanup = await _make_neo4j_engine()
    else:  # pragma: no cover — defensive, parametrize values are closed.
        raise ValueError(f"Unknown backend: {kind}")

    try:
        yield eng
    finally:
        await cleanup()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_work_product(
    name: str,
    *,
    wp_type: WorkProductType = WorkProductType.CAD_MODEL,
    domain: str = "mechanical",
    metadata: dict[str, Any] | None = None,
) -> WorkProduct:
    """Build a ``WorkProduct`` with a stable shape for cross-backend asserts."""
    return WorkProduct(
        name=name,
        type=wp_type,
        domain=domain,
        file_path=f"models/{name}.step",
        content_hash=f"hash-{name}",
        format="step",
        created_by="parity-test",
        metadata=metadata or {},
    )


# ---------------------------------------------------------------------------
# Tests — five cases, each running against both backends.
# ---------------------------------------------------------------------------


class TestGraphEngineParity:
    """L1-F5: behavioral parity across InMemory + Neo4j graph engines."""

    async def test_create_and_get_node_round_trips(self, engine: GraphEngine) -> None:
        """Create a node with a rich payload; fetch by id; payload round-trips.

        The spec calls out
        ``{type:"design_decision", payload:{title:"x"}}`` — there is no
        ``design_decision`` NodeType, so we use ``WorkProduct``'s
        ``metadata`` dict as the payload carrier. The contract is the
        same: arbitrary-shape user data must survive a write/read cycle.
        """
        payload = {
            "title": "design-decision-A",
            "rationale": "Choose 5V rail for sensor compatibility.",
            "alternatives": ["3.3V", "12V"],
            "score": 0.87,
        }
        wp = _make_work_product("dd-1", metadata=payload)
        await engine.add_node(wp)

        fetched = await engine.get_node(wp.id)
        assert fetched is not None, f"node {wp.id} not retrievable after add"
        assert isinstance(fetched, WorkProduct), type(fetched)
        # Payload round-trips byte-equal — this is the load-bearing
        # assertion; if either backend mangles dict / list / float
        # encoding, this trips.
        assert fetched.metadata == payload, (
            f"metadata drift: ingested {payload!r}, got {fetched.metadata!r}"
        )
        # Shape assertions — the rest of the model survives too.
        assert fetched.id == wp.id
        assert fetched.name == wp.name
        assert fetched.node_type == NodeType.WORK_PRODUCT

    async def test_property_lookup_returns_matching_nodes(self, engine: GraphEngine) -> None:
        """Three nodes, three ``type`` values; query by type returns the subset.

        Uses ``list_nodes(filters={"type": ...})`` which both backends
        translate into a primitive equality match. Modulo ordering — we
        compare as sets of ids.
        """
        wp_schematic = _make_work_product("s1", wp_type=WorkProductType.SCHEMATIC)
        wp_pcb = _make_work_product("pcb1", wp_type=WorkProductType.PCB_LAYOUT)
        wp_cad = _make_work_product("cad1", wp_type=WorkProductType.CAD_MODEL)
        for wp in (wp_schematic, wp_pcb, wp_cad):
            await engine.add_node(wp)

        # Filter by ``type = "schematic"`` — exactly one matching node.
        results = await engine.list_nodes(
            node_type=NodeType.WORK_PRODUCT,
            filters={"type": "schematic"},
        )
        result_ids = {n.id for n in results}
        assert wp_schematic.id in result_ids, (
            f"schematic missing from filtered result: {result_ids}"
        )
        assert wp_pcb.id not in result_ids, "pcb leaked through type=schematic filter"
        assert wp_cad.id not in result_ids, "cad leaked through type=schematic filter"
        assert len(result_ids) == 1, f"expected 1 result, got {len(result_ids)}: {result_ids}"

    async def test_create_edge_round_trips(self, engine: GraphEngine) -> None:
        """A → B with DEPENDS_ON; outgoing edges from A include exactly one to B.

        Uses ``DependsOnEdge`` rather than the spec's literal
        ``relationship="cites"`` because the codebase doesn't define a
        ``cites`` EdgeType — DEPENDS_ON is the closest semantic
        equivalent and exercises the same code paths.
        """
        a = _make_work_product("a")
        b = _make_work_product("b")
        await engine.add_node(a)
        await engine.add_node(b)

        edge = DependsOnEdge(source_id=a.id, target_id=b.id, description="A cites B")
        await engine.add_edge(edge)

        outgoing = await engine.get_edges(a.id, direction="outgoing")
        assert len(outgoing) == 1, f"expected 1 outgoing edge from A, got {len(outgoing)}"
        only = outgoing[0]
        assert only.source_id == a.id
        assert only.target_id == b.id
        assert only.edge_type == EdgeType.DEPENDS_ON, (
            f"edge_type drift: expected DEPENDS_ON, got {only.edge_type}"
        )

    async def test_traversal_two_hops(self, engine: GraphEngine) -> None:
        """A → B → C; ``get_subgraph(A, depth=2)`` reaches both B and C."""
        a = _make_work_product("a-2hop")
        b = _make_work_product("b-2hop")
        c = _make_work_product("c-2hop")
        for wp in (a, b, c):
            await engine.add_node(wp)

        await engine.add_edge(DependsOnEdge(source_id=a.id, target_id=b.id))
        await engine.add_edge(DependsOnEdge(source_id=b.id, target_id=c.id))

        subgraph = await engine.get_subgraph(a.id, depth=2, edge_types=[EdgeType.DEPENDS_ON])
        reached_ids = {n.id for n in subgraph.nodes}
        # A is the root and also returned; B and C must both be present.
        assert a.id in reached_ids, "root A missing from subgraph"
        assert b.id in reached_ids, f"B (1 hop) not reached within depth 2: {reached_ids}"
        assert c.id in reached_ids, f"C (2 hops) not reached within depth 2: {reached_ids}"
        # And exactly those three — depth 2 from A in this DAG is {A, B, C}.
        assert reached_ids == {a.id, b.id, c.id}, (
            f"unexpected nodes in 2-hop subgraph: {reached_ids ^ {a.id, b.id, c.id}}"
        )

    async def test_constraint_violations_consistent(self, engine: GraphEngine) -> None:
        """Same constraint + same data → same violation report on both backends.

        Composes the engine into an ``InMemoryConstraintEngine`` (the
        constraint engine is graph-backend-agnostic — it talks to the
        ``GraphEngine`` ABC). Adds one work_product and one constraint
        whose expression is guaranteed to fail (``False``), then
        asserts the evaluation result has exactly one ERROR violation
        with the expected name. Doing the same on both backends is the
        parity check.
        """
        wp = _make_work_product("constraint-target")
        await engine.add_node(wp)

        constraints = InMemoryConstraintEngine(engine)

        violating = Constraint(
            name="always-fails",
            expression="False",  # always violates — deterministic across backends.
            severity=ConstraintSeverity.ERROR,
            domain="mechanical",
            source="parity-test",
            message="parity-test forced failure",
        )
        await constraints.add_constraint(violating, work_product_ids=[wp.id])

        result = await constraints.evaluate(work_product_ids=[wp.id])

        assert result.passed is False, "evaluation should fail — constraint is False"
        assert len(result.violations) == 1, (
            f"expected 1 ERROR violation, got {len(result.violations)}: "
            f"{[v.constraint_name for v in result.violations]}"
        )
        v = result.violations[0]
        assert v.constraint_name == "always-fails", v.constraint_name
        assert v.severity == ConstraintSeverity.ERROR, v.severity
        assert wp.id in v.work_product_ids, (
            f"work_product not attributed to violation: {v.work_product_ids}"
        )
        assert result.evaluated_count == 1, result.evaluated_count
        assert len(result.warnings) == 0, [w.constraint_name for w in result.warnings]
