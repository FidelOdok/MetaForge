"""Unit tests for the TwinAPI facade (InMemoryTwinAPI)."""

from uuid import UUID, uuid4

import pytest

from twin_core.api import InMemoryTwinAPI, OrphanWouldBeCreatedError
from twin_core.constraint_engine.validator import ConstraintEngine
from twin_core.models import (
    Component,
    Constraint,
    ConstraintSeverity,
    Datasheet,
    EdgeType,
    WorkProduct,
    WorkProductType,
)


@pytest.fixture
def api():
    return InMemoryTwinAPI.create()


def _make_datasheet(
    mpn: str = "STM32H745ZIT6",
    manufacturer: str = "STMicroelectronics",
    revision: str = "rev1",
    file_hash: str | None = None,
) -> Datasheet:
    return Datasheet(
        mpn=mpn,
        manufacturer=manufacturer,
        revision=revision,
        file_hash=file_hash or f"hash-{mpn}-{revision}",
        source_path=f"datasheets/{mpn.lower()}_{revision}.pdf",
        page_count=200,
    )


def _make_work_product(name: str = "test", domain: str = "mechanical") -> WorkProduct:
    return WorkProduct(
        name=name,
        type=WorkProductType.CAD_MODEL,
        domain=domain,
        file_path=f"models/{name}.step",
        content_hash="hash123",
        format="step",
        created_by="human",
    )


def _make_constraint(
    name: str = "test_constraint",
    domain: str = "mechanical",
    expression: str = "True",
) -> Constraint:
    return Constraint(
        name=name,
        expression=expression,
        severity=ConstraintSeverity.ERROR,
        domain=domain,
        source="user",
    )


# --- Subsystem accessors (MET-424) ---


class TestSubsystemAccessors:
    def test_constraints_returns_engine(self, api):
        """``twin.constraints`` exposes the live ``ConstraintEngine``.

        Regression for MET-424 — the MCP / gateway bootstrap previously
        reached into ``twin._constraints`` to wire the constraint adapter
        because no public accessor existed.
        """
        assert isinstance(api.constraints, ConstraintEngine)
        # Same instance every call — accessor must not allocate.
        assert api.constraints is api.constraints


# --- Graph hygiene (MET-429) ---


class TestFindOrphans:
    """`TwinAPI.find_orphans()` surfaces dependent nodes with zero edges."""

    async def test_empty_graph_is_clean(self, api):
        report = await api.find_orphans()
        assert report.total == 0
        assert report.is_clean
        assert report.orphan_constraints == []
        assert report.orphan_bom_items == []
        assert report.orphan_design_elements == []
        assert report.orphan_components == []

    async def test_isolated_constraint_is_orphan(self, api):
        c = _make_constraint()
        await api.create_constraint(c)
        # No bindings created — the constraint floats with zero edges.

        report = await api.find_orphans()
        assert report.orphan_constraints == [c.id]
        assert report.total == 1
        assert report.is_clean is False

    async def test_bound_constraint_is_not_orphan(self, api):
        wp = _make_work_product()
        await api.create_work_product(wp)
        c = _make_constraint()
        await api.constraints.add_constraint(c, [wp.id])

        report = await api.find_orphans()
        assert report.orphan_constraints == []
        assert report.total == 0

    async def test_isolated_component_is_orphan(self, api):
        comp = Component(part_number="STM32-X", manufacturer="ST")
        await api.add_component(comp)

        report = await api.find_orphans()
        assert report.orphan_components == [comp.id]

    async def test_default_delete_blocks_when_would_create_orphan(self, api):
        """MET-438: default-deny — delete_work_product without cascade refuses
        to leave dependent nodes dangling.

        Pre-MET-438 behaviour would silently orphan the Constraint and
        rely on find_orphans() to catch it later. The block makes the
        problem visible at the delete call.
        """
        wp = _make_work_product()
        await api.create_work_product(wp)
        c = _make_constraint()
        await api.constraints.add_constraint(c, [wp.id])

        with pytest.raises(OrphanWouldBeCreatedError) as exc:
            await api.delete_work_product(wp.id)

        assert exc.value.work_product_id == wp.id
        assert c.id in exc.value.orphans.orphan_constraints
        # WorkProduct was NOT deleted (atomic rejection).
        assert await api.get_work_product(wp.id) is not None

    async def test_cascade_delete_removes_dependents_and_target(self, api):
        """MET-438: explicit cascade=True wipes the dependents that would orphan."""
        wp = _make_work_product()
        await api.create_work_product(wp)
        c = _make_constraint()
        await api.constraints.add_constraint(c, [wp.id])

        deleted = await api.delete_work_product(wp.id, cascade=True)
        assert deleted is True

        assert await api.get_work_product(wp.id) is None
        # Constraint also gone.
        report = await api.find_orphans()
        assert c.id not in report.orphan_constraints
        assert await api._graph.get_node(c.id) is None

    async def test_delete_with_no_dependents_unchanged_by_cascade_flag(self, api):
        """No dependents → both cascade=False and True are simple deletes."""
        wp = _make_work_product()
        await api.create_work_product(wp)

        # Default still works when no dependents would orphan.
        deleted = await api.delete_work_product(wp.id)
        assert deleted is True
        assert await api.get_work_product(wp.id) is None

    async def test_dependent_with_other_edges_is_not_blocking(self, api):
        """A Constraint bound to two WPs survives one WP delete without cascade.

        Only dependents whose **only** neighbour is the target work
        product are considered "would orphan". When a Constraint has
        edges to other WPs, deleting one doesn't endanger it.
        """
        wp_a = _make_work_product(name="a")
        wp_b = _make_work_product(name="b")
        await api.create_work_product(wp_a)
        await api.create_work_product(wp_b)
        c = _make_constraint()
        await api.constraints.add_constraint(c, [wp_a.id, wp_b.id])

        # Delete wp_a — Constraint still bound to wp_b, no orphan risk.
        deleted = await api.delete_work_product(wp_a.id)
        assert deleted is True
        assert await api._graph.get_node(c.id) is not None

    async def test_work_products_are_not_dependents(self, api):
        """WorkProduct nodes are not orphans even when they have no edges.

        WorkProducts are roots — they don't need an incoming reference
        to be valid. Only dependent types (Constraint, BOMItem,
        DesignElement, Component) are flagged.
        """
        wp = _make_work_product()
        await api.create_work_product(wp)

        report = await api.find_orphans()
        assert report.is_clean

    async def test_find_orphans_emits_metrics_per_kind(self):
        """MET-439: ``find_orphans`` calls ``set_twin_orphans`` once per kind.

        The collector is optional. When supplied, every scan reports
        the per-kind count so the ``metaforge_twin_orphans`` gauge
        reflects the most recent state.
        """
        recorded: list[tuple[str, int]] = []

        class _StubCollector:
            def set_twin_orphans(self, kind: str, count: int) -> None:
                recorded.append((kind, count))

        api = InMemoryTwinAPI.create()
        api._collector = _StubCollector()  # type: ignore[assignment]

        # 1 orphan constraint, 0 of every other kind.
        c = _make_constraint()
        await api.create_constraint(c)

        await api.find_orphans()

        # One entry per dependent kind, with the right counts.
        assert recorded == [
            ("constraint", 1),
            ("bom_item", 0),
            ("design_element", 0),
            ("component", 0),
        ]


# --- Project partitioning (MET-428) ---


class TestProjectPartitioning:
    """``list_work_products`` filters by ``project_id`` (MET-428).

    This is the Phase 1 in-memory slice. Neo4j-side index + Cypher
    safety + MCP context forwarding ship as follow-ups under the same
    ticket.
    """

    async def test_list_returns_all_when_no_filter(self, api):
        project_a = uuid4()
        project_b = uuid4()
        wp_a = _make_work_product_in_project(project_a, name="wp-a")
        wp_b = _make_work_product_in_project(project_b, name="wp-b")
        await api.create_work_product(wp_a)
        await api.create_work_product(wp_b)

        all_wps = await api.list_work_products()
        ids = {wp.id for wp in all_wps}
        assert wp_a.id in ids
        assert wp_b.id in ids

    async def test_list_filters_to_project(self, api):
        project_a = uuid4()
        project_b = uuid4()
        wp_a = _make_work_product_in_project(project_a, name="wp-a")
        wp_b = _make_work_product_in_project(project_b, name="wp-b")
        await api.create_work_product(wp_a)
        await api.create_work_product(wp_b)

        scoped = await api.list_work_products(project_id=project_a)
        assert [wp.id for wp in scoped] == [wp_a.id]

    async def test_cross_project_read_is_impossible(self, api):
        """The headline isolation guarantee from MET-428.

        Ingest under project A, then query under project B → must
        return zero rows from A.
        """
        project_a = uuid4()
        project_b = uuid4()
        wp = _make_work_product_in_project(project_a, name="secret-a")
        await api.create_work_product(wp)

        under_b = await api.list_work_products(project_id=project_b)
        assert under_b == []

    async def test_project_id_combines_with_domain_filter(self, api):
        project_a = uuid4()
        mech = _make_work_product_in_project(project_a, name="mech-wp", domain="mechanical")
        ee = _make_work_product_in_project(project_a, name="ee-wp", domain="electronics")
        await api.create_work_product(mech)
        await api.create_work_product(ee)

        scoped = await api.list_work_products(project_id=project_a, domain="mechanical")
        assert [wp.id for wp in scoped] == [mech.id]


def _make_work_product_in_project(
    project_id: UUID,
    *,
    name: str = "test",
    domain: str = "mechanical",
) -> WorkProduct:
    """Like ``_make_work_product`` but pinned to a project (MET-428)."""
    return WorkProduct(
        name=name,
        type=WorkProductType.CAD_MODEL,
        domain=domain,
        file_path=f"models/{name}.step",
        content_hash="hash123",
        format="step",
        created_by="human",
        project_id=project_id,
    )


# --- Datasheets (MET-430) ---


class TestDatasheetIngest:
    """`TwinAPI.ingest_datasheet` — versioning + idempotency."""

    async def test_first_ingest_persists(self, api):
        ds = _make_datasheet(revision="rev1")
        result = await api.ingest_datasheet(ds)
        assert result.id == ds.id
        assert result.revision == "rev1"

    async def test_idempotent_on_same_file_hash(self, api):
        ds_a = _make_datasheet(revision="rev1", file_hash="sha-shared")
        ds_b = _make_datasheet(revision="rev2-different", file_hash="sha-shared")

        first = await api.ingest_datasheet(ds_a)
        second = await api.ingest_datasheet(ds_b)
        # Same file_hash → second call is a no-op, returns the original.
        assert second.id == first.id
        assert second.revision == "rev1"

    async def test_new_revision_supersedes_old(self, api):
        old = _make_datasheet(revision="rev1", file_hash="sha-old")
        new = _make_datasheet(revision="rev2", file_hash="sha-new")

        await api.ingest_datasheet(old)
        await api.ingest_datasheet(new)

        # The new revision has an outgoing SUPERSEDES edge pointing at old.
        edges = await api._graph.get_edges(
            new.id, direction="outgoing", edge_type=EdgeType.SUPERSEDES
        )
        assert len(edges) == 1
        assert edges[0].target_id == old.id

    async def test_current_datasheet_follows_supersedes_chain(self, api):
        v1 = _make_datasheet(revision="rev1", file_hash="sha-1")
        v2 = _make_datasheet(revision="rev2", file_hash="sha-2")
        v3 = _make_datasheet(revision="rev3", file_hash="sha-3")
        await api.ingest_datasheet(v1)
        await api.ingest_datasheet(v2)
        await api.ingest_datasheet(v3)

        current = await api.get_current_datasheet(v1.mpn)
        assert current is not None
        assert current.id == v3.id

    async def test_find_datasheets_returns_all_revisions(self, api):
        await api.ingest_datasheet(_make_datasheet(revision="rev1", file_hash="a"))
        await api.ingest_datasheet(_make_datasheet(revision="rev2", file_hash="b"))
        await api.ingest_datasheet(_make_datasheet(revision="rev3", file_hash="c"))

        all_revs = await api.find_datasheets_by_mpn("STM32H745ZIT6")
        revisions = {d.revision for d in all_revs}
        assert revisions == {"rev1", "rev2", "rev3"}

    async def test_get_current_returns_none_for_unknown_mpn(self, api):
        result = await api.get_current_datasheet("NOT-A-REAL-MPN")
        assert result is None

    async def test_describes_edge_links_to_matching_component(self, api):
        """Ingesting a Datasheet links it to every Component sharing its MPN."""
        comp = Component(part_number="STM32H745ZIT6", manufacturer="STMicroelectronics")
        await api.add_component(comp)

        ds = _make_datasheet(mpn="STM32H745ZIT6")
        await api.ingest_datasheet(ds)

        edges = await api._graph.get_edges(
            ds.id, direction="outgoing", edge_type=EdgeType.DESCRIBES
        )
        assert len(edges) == 1
        assert edges[0].target_id == comp.id

    async def test_describes_edge_skipped_when_no_component(self, api):
        """No Component exists yet → no auto-edge (no node auto-creation)."""
        ds = _make_datasheet(mpn="UNKNOWN-MPN")
        await api.ingest_datasheet(ds)

        edges = await api._graph.get_edges(
            ds.id, direction="outgoing", edge_type=EdgeType.DESCRIBES
        )
        assert edges == []

    async def test_different_mpns_are_independent(self, api):
        a = _make_datasheet(mpn="STM32H745ZIT6", revision="rev1", file_hash="a")
        b = _make_datasheet(mpn="ESP32-WROOM-32E", revision="rev1", file_hash="b")
        await api.ingest_datasheet(a)
        await api.ingest_datasheet(b)

        current_a = await api.get_current_datasheet("STM32H745ZIT6")
        current_b = await api.get_current_datasheet("ESP32-WROOM-32E")
        assert current_a is not None and current_a.id == a.id
        assert current_b is not None and current_b.id == b.id
        # Crucially — no SUPERSEDES edge was created between unrelated MPNs.
        edges = await api._graph.get_edges(
            b.id, direction="outgoing", edge_type=EdgeType.SUPERSEDES
        )
        assert edges == []


# --- Lifecycle (MET-425) ---


class TestLifecycle:
    async def test_aclose_in_memory_is_a_noop(self):
        """In-memory graph has no ``close`` — aclose must not raise."""
        twin = InMemoryTwinAPI.create()
        await twin.aclose()

    async def test_aclose_invokes_graph_close(self):
        """When the graph exposes ``close()``, aclose awaits it.

        Regression for MET-425 — the stdio entrypoint must close the
        Neo4j driver on exit to avoid dangling bolt connections across
        subprocess respawns.
        """
        import asyncio

        from twin_core.constraint_engine.validator import InMemoryConstraintEngine
        from twin_core.graph_engine import InMemoryGraphEngine
        from twin_core.versioning.branch import InMemoryVersionEngine

        graph = InMemoryGraphEngine()
        closed = asyncio.Event()

        async def _fake_close() -> None:
            closed.set()

        graph.close = _fake_close  # type: ignore[attr-defined]

        twin = InMemoryTwinAPI(
            graph=graph,
            version=InMemoryVersionEngine(graph),
            constraints=InMemoryConstraintEngine(graph),
        )
        await twin.aclose()
        assert closed.is_set()


# --- WorkProduct operations ---


class TestArtifactOperations:
    async def test_create_and_get_work_product(self, api):
        a = _make_work_product()
        created = await api.create_work_product(a)
        assert created.id == a.id
        assert created.name == "test"

        fetched = await api.get_work_product(a.id)
        assert fetched is not None
        assert fetched.id == a.id

    async def test_update_work_product(self, api):
        a = _make_work_product()
        await api.create_work_product(a)

        updated = await api.update_work_product(a.id, {"name": "updated"})
        assert updated.name == "updated"

        fetched = await api.get_work_product(a.id)
        assert fetched is not None
        assert fetched.name == "updated"

    async def test_delete_work_product(self, api):
        a = _make_work_product()
        await api.create_work_product(a)

        result = await api.delete_work_product(a.id)
        assert result is True

        fetched = await api.get_work_product(a.id)
        assert fetched is None

    async def test_list_work_products_filtered_by_domain(self, api):
        a = _make_work_product("mech", domain="mechanical")
        b = _make_work_product("elec", domain="electronics")
        await api.create_work_product(a)
        await api.create_work_product(b)

        results = await api.list_work_products(domain="electronics")
        assert len(results) == 1
        assert results[0].id == b.id

    async def test_list_work_products_filtered_by_type(self, api):
        a = _make_work_product("a")
        b = WorkProduct(
            name="schematic",
            type=WorkProductType.SCHEMATIC,
            domain="electronics",
            file_path="eda/main.kicad_sch",
            content_hash="hash456",
            format="kicad",
            created_by="human",
        )
        await api.create_work_product(a)
        await api.create_work_product(b)

        results = await api.list_work_products(work_product_type=WorkProductType.SCHEMATIC)
        assert len(results) == 1
        assert results[0].id == b.id

    async def test_get_work_product_not_found_returns_none(self, api):
        result = await api.get_work_product(uuid4())
        assert result is None


# --- Constraint operations ---


class TestConstraintOperations:
    async def test_create_and_get_constraint(self, api):
        c = _make_constraint()
        created = await api.create_constraint(c)
        assert created.id == c.id

        fetched = await api.get_constraint(c.id)
        assert fetched is not None
        assert fetched.name == "test_constraint"

    async def test_evaluate_constraints_all_pass(self, api):
        # Create work_product and constraint that passes
        a = _make_work_product()
        await api.create_work_product(a)

        c = _make_constraint(expression="True")
        await api.constraints.add_constraint(c, [a.id])

        result = await api.evaluate_constraints()
        assert result.passed is True
        assert result.evaluated_count == 1

    async def test_evaluate_constraints_with_violation(self, api):
        a = _make_work_product()
        await api.create_work_product(a)

        c = _make_constraint(expression="False")
        await api.constraints.add_constraint(c, [a.id])

        result = await api.evaluate_constraints()
        assert result.passed is False
        assert len(result.violations) == 1
        assert result.violations[0].constraint_id == c.id


# --- Component operations ---


class TestComponentOperations:
    async def test_add_and_get_component(self, api):
        comp = Component(part_number="STM32F407", manufacturer="ST")
        created = await api.add_component(comp)
        assert created.id == comp.id

        fetched = await api.get_component(comp.id)
        assert fetched is not None
        assert fetched.part_number == "STM32F407"

    async def test_find_components_by_query(self, api):
        comp_a = Component(part_number="STM32F407", manufacturer="ST")
        comp_b = Component(part_number="ESP32-S3", manufacturer="Espressif")
        await api.add_component(comp_a)
        await api.add_component(comp_b)

        results = await api.find_components({"manufacturer": "ST"})
        assert len(results) == 1
        assert results[0].id == comp_a.id

    async def test_get_component_not_found_returns_none(self, api):
        result = await api.get_component(uuid4())
        assert result is None


# --- Relationship operations ---


class TestRelationshipOperations:
    async def test_add_and_get_edges(self, api):
        a = _make_work_product("a")
        b = _make_work_product("b")
        await api.create_work_product(a)
        await api.create_work_product(b)

        edge = await api.add_edge(a.id, b.id, EdgeType.DEPENDS_ON)
        assert edge.source_id == a.id
        assert edge.target_id == b.id

        edges = await api.get_edges(a.id, direction="outgoing")
        assert len(edges) == 1
        assert edges[0].target_id == b.id

    async def test_remove_edge(self, api):
        a = _make_work_product("a")
        b = _make_work_product("b")
        await api.create_work_product(a)
        await api.create_work_product(b)

        await api.add_edge(a.id, b.id, EdgeType.DEPENDS_ON)
        result = await api.remove_edge(a.id, b.id, EdgeType.DEPENDS_ON)
        assert result is True

        edges = await api.get_edges(a.id, direction="outgoing")
        assert len(edges) == 0


# --- Query operations ---


class TestQueryOperations:
    async def test_get_subgraph(self, api):
        a = _make_work_product("a")
        b = _make_work_product("b")
        await api.create_work_product(a)
        await api.create_work_product(b)

        await api.add_edge(a.id, b.id, EdgeType.DEPENDS_ON)

        sg = await api.get_subgraph(a.id, depth=2)
        assert sg.root_id == a.id
        assert len(sg.nodes) == 2
        assert len(sg.edges) == 1

    async def test_query_cypher_raises_not_implemented(self, api):
        with pytest.raises(NotImplementedError, match="Neo4j"):
            await api.query_cypher("MATCH (n) RETURN n")


# --- Versioning operations ---


class TestVersioningOperations:
    async def test_create_branch(self, api):
        # Initialize main with a commit first
        await api._version.create_branch("main")
        a = _make_work_product()
        await api.create_work_product(a)
        await api._version.commit("main", "init", [a.id], "test")

        branch = await api.create_branch("feature")
        assert branch == "feature"

    async def test_commit_and_log(self, api):
        await api._version.create_branch("main")
        a = _make_work_product()
        await api.create_work_product(a)
        await api._version.commit("main", "init", [a.id], "test")

        version = await api.commit("main", "second commit", "tester")
        assert version.branch_name == "main"
        assert version.commit_message == "second commit"

        history = await api.log("main")
        assert len(history) == 2
        assert history[0].commit_message == "second commit"

    async def test_merge_branches(self, api):
        await api._version.create_branch("main")
        a = _make_work_product()
        await api.create_work_product(a)
        await api._version.commit("main", "init", [a.id], "test")

        await api.create_branch("feature")
        b = _make_work_product("b")
        await api.create_work_product(b)
        await api._version.commit("feature", "add b", [b.id], "test")

        merge_version = await api.merge("feature", "main", "merge feature", "tester")
        assert merge_version.branch_name == "main"
        assert merge_version.commit_message == "merge feature"

    async def test_diff_branches(self, api):
        await api._version.create_branch("main")
        a = _make_work_product("a")
        await api.create_work_product(a)
        await api._version.commit("main", "init", [a.id], "test")

        await api.create_branch("feature")
        b = _make_work_product("b")
        await api.create_work_product(b)
        await api._version.commit("feature", "add b", [b.id], "test")

        diff_result = await api.diff("main", "feature")
        assert diff_result.version_a is not None
        assert diff_result.version_b is not None
        assert len(diff_result.changes) > 0
