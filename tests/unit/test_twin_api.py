"""Unit tests for the TwinAPI facade (InMemoryTwinAPI)."""

from uuid import uuid4

import pytest

from twin_core.api import InMemoryTwinAPI
from twin_core.models import (
    Component,
    Constraint,
    ConstraintSeverity,
    EdgeType,
    WorkProduct,
    WorkProductType,
)


@pytest.fixture
def api():
    return InMemoryTwinAPI.create()


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
        # Use the constraint engine directly to bind constraint to work_product
        await api._constraints.add_constraint(c, [a.id])

        result = await api.evaluate_constraints()
        assert result.passed is True
        assert result.evaluated_count == 1

    async def test_evaluate_constraints_with_violation(self, api):
        a = _make_work_product()
        await api.create_work_product(a)

        c = _make_constraint(expression="False")
        await api._constraints.add_constraint(c, [a.id])

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
