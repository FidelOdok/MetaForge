"""The relationships view names the far end of every edge (FORGE-704).

On the sandbox 641 edges came back and the dashboard could draw 229: the rest
ran to constraints, baselines, BOM items and engineering entities, which the
view did not list, so the dashboard dropped them without saying so. The route now
names those ends, includes edges that point *into* a work product, and reports
the work products it could not read.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from api_gateway.twin import edge_endpoints, routes
from tests.unit.test_twin_list_scoping import _seed, _wp
from twin_core.models.constraint import Constraint
from twin_core.models.engineering_entity import EngineeringEntity
from twin_core.models.enums import ConstraintSeverity, EdgeType


def _constraint(name: str) -> Constraint:
    return Constraint(
        name=name,
        expression="x > 0",
        severity=ConstraintSeverity.ERROR,
        domain="mechanical",
        source="test",
    )


def _entity(title: str, kind: str) -> EngineeringEntity:
    return EngineeringEntity(entity_type=kind, title=title, created_at=datetime.now(UTC))


class TestSummarise:
    def test_a_constraint_is_named_by_its_name(self) -> None:
        node = SimpleNamespace(node_type="constraint", name="Max mass 250 g")
        out = edge_endpoints.summarise(node, str(uuid4()))
        assert (out.kind, out.name) == ("constraint", "Max mass 250 g")

    def test_an_engineering_entity_reports_its_entity_type_as_the_kind(self) -> None:
        node = SimpleNamespace(
            node_type="engineering_entity", entity_type="intent", title=None, statement="Hover"
        )
        out = edge_endpoints.summarise(node, str(uuid4()))
        assert (out.kind, out.name) == ("intent", "Hover")

    def test_a_bom_item_falls_back_to_its_part_number(self) -> None:
        node = SimpleNamespace(node_type="bom_item", part_number="XING2-1404", description="")
        assert edge_endpoints.summarise(node, str(uuid4())).name == "XING2-1404"

    def test_a_node_with_no_name_field_shows_a_short_id(self) -> None:
        node_id = str(uuid4())
        out = edge_endpoints.summarise(SimpleNamespace(node_type="baseline"), node_id)
        assert out.name == node_id[:8]

    def test_a_long_name_is_cut_and_whitespace_collapsed(self) -> None:
        node = SimpleNamespace(node_type="constraint", name="a   b\n" + "x" * 200)
        name = edge_endpoints.summarise(node, str(uuid4())).name
        assert len(name) == 80 and name.endswith("…") and "\n" not in name


class TestRoute:
    async def test_an_edge_to_a_constraint_is_listed_and_its_far_end_named(self) -> None:
        wp = _wp("Arm", None)
        twin = await _seed(wp)
        constraint = await twin.create_constraint(_constraint("Deflection under 1 mm"))
        await twin.add_edge(wp.id, constraint.id, EdgeType.CONSTRAINED_BY)

        resp = await routes.list_twin_relationships()

        assert resp.total == 1
        ends = resp.endpoints
        assert set(ends) == {str(constraint.id)}
        assert (ends[str(constraint.id)].kind, ends[str(constraint.id)].name) == (
            "constraint",
            "Deflection under 1 mm",
        )

    async def test_work_product_to_work_product_edges_need_no_endpoints(self) -> None:
        a, b = _wp("A", None), _wp("B", None)
        twin = await _seed(a, b)
        await twin.add_edge(a.id, b.id, EdgeType.DEPENDS_ON)

        resp = await routes.list_twin_relationships()

        assert resp.total == 1 and resp.endpoints == {}

    async def test_an_edge_pointing_into_a_work_product_is_listed(self) -> None:
        """Outgoing only meant an entity that depends on a work product, or a
        record generated from one, was never requested (120 edges on the sandbox)."""
        wp = _wp("Bracket", None)
        twin = await _seed(wp)
        entity = await twin.create_engineering_entity(_entity("Stay under 250 g", "intent"))
        await twin.add_edge(entity.id, wp.id, EdgeType.DEPENDS_ON)

        resp = await routes.list_twin_relationships()

        assert [(r.sourceId, r.targetId, r.type) for r in resp.relationships] == [
            (str(entity.id), str(wp.id), "depends_on")
        ]
        assert resp.endpoints[str(entity.id)].kind == "intent"
        assert resp.endpoints[str(entity.id)].name == "Stay under 250 g"

    async def test_an_edge_seen_from_both_ends_is_listed_once(self) -> None:
        a, b = _wp("A", None), _wp("B", None)
        twin = await _seed(a, b)
        await twin.add_edge(a.id, b.id, EdgeType.PARENT_OF)

        assert (await routes.list_twin_relationships()).total == 1

    async def test_an_end_that_cannot_be_read_is_reported_as_unknown_not_dropped(self) -> None:
        wp = _wp("A", None)
        twin = await _seed(wp)
        ghost = await twin.create_constraint(_constraint("will be unreadable"))
        await twin.add_edge(wp.id, ghost.id, EdgeType.CONSTRAINED_BY)

        async def broken(_node_id):  # noqa: ANN001
            raise RuntimeError("graph read failed")

        twin.graph.get_node = broken  # type: ignore[method-assign]

        resp = await routes.list_twin_relationships()

        assert resp.total == 1
        assert resp.endpoints[str(ghost.id)].kind == "unknown"

    async def test_a_work_product_whose_edges_cannot_be_read_is_counted(self) -> None:
        good, bad = _wp("Good", None), _wp("Bad", None)
        twin = await _seed(good, bad)
        original = twin.get_edges

        async def flaky(node_id, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
            if node_id == bad.id:
                raise RuntimeError("edge read failed")
            return await original(node_id, *args, **kwargs)

        twin.get_edges = flaky  # type: ignore[method-assign]

        resp = await routes.list_twin_relationships()

        assert resp.skipped_nodes == 1

    async def test_a_clean_run_reports_no_skipped_nodes(self) -> None:
        await _seed(_wp("A", None))
        assert (await routes.list_twin_relationships()).skipped_nodes == 0
