"""Item identity and immutable revisions (FORGE-523).

Component-level: the real recorders over ``InMemoryTwinAPI``, with only the
MinIO blob store patched.
"""

from __future__ import annotations

import base64
from uuid import UUID, uuid4

import pytest

from api_gateway.twin.bom_recorder import make_bom_recorder
from api_gateway.twin.component_recorder import make_component_recorder
from api_gateway.twin.constraint_recorder import make_constraint_recorder
from api_gateway.twin.engineering_entity_recorder import make_engineering_entity_recorder
from api_gateway.twin.geometry_recorder import make_geometry_recorder
from api_gateway.twin.item_revisions import make_item_history_reader
from twin_core.api import InMemoryTwinAPI
from twin_core.items import (
    ItemError,
    ItemRevisionConflictError,
    UnknownItemError,
    derive_key,
    find_item,
    item_history,
    list_items,
    parse_item_ref,
    resolve_item_ref,
)
from twin_core.models.enums import EdgeType, WorkProductType
from twin_core.models.item import Item
from twin_core.models.work_product import WorkProduct

PROJECT = "55555555-5555-5555-5555-555555555555"


def _step(body: str) -> str:
    return base64.b64encode(f"ISO-10303-21;\n{body}\nENDSEC;\n".encode()).decode("ascii")


@pytest.fixture(autouse=True)
def _blob_store(monkeypatch: pytest.MonkeyPatch) -> None:
    import digital_twin.storage.work_product_blobs as blobs

    monkeypatch.setattr(
        blobs,
        "store_work_product_blob",
        lambda node_id, filename, content, content_type="": f"wp/{node_id}/{filename}",
    )


@pytest.fixture
def twin() -> InMemoryTwinAPI:
    return InMemoryTwinAPI.create()


async def _heads(twin: InMemoryTwinAPI, item: Item) -> list[UUID]:
    edges = await twin.graph.get_edges(item.id, direction="outgoing", edge_type=EdgeType.HEAD)
    return [e.target_id for e in edges]


class TestKeys:
    def test_parse_bare_and_pinned(self) -> None:
        assert parse_item_ref("cad-bracket") == ("CAD-BRACKET", None)
        assert parse_item_ref(" CAD-BRACKET@3 ") == ("CAD-BRACKET", 3)

    @pytest.mark.parametrize("bad", ["", "CAD@x", "CAD@0", "has space", "@2"])
    def test_parse_rejects(self, bad: str) -> None:
        with pytest.raises(ItemError):
            parse_item_ref(bad)

    def test_derive_key_uses_type_prefix(self) -> None:
        assert derive_key("cad_model", "Upper Arm Link") == "CAD-UPPER-ARM-LINK"
        assert derive_key("constraint_set", "Shelf reqs v2") == "CS-SHELF-REQS-V2"
        assert derive_key("intent", "!!!") == "INT-UNNAMED"


class TestGeometryRevisions:
    async def test_first_commit_creates_item_at_revision_one(self, twin) -> None:
        record = make_geometry_recorder(twin, None)
        out = await record(step_base64=_step("a"), name="Shelf Bracket", project_id=PROJECT)

        assert out["item_key"] == "CAD-SHELF-BRACKET"
        assert out["revision"] == 1
        assert out["item_ref"] == "CAD-SHELF-BRACKET@1"
        item = await find_item(twin, "CAD-SHELF-BRACKET", PROJECT)
        assert item is not None
        assert item.item_type == "cad_model"
        assert item.head_revision == 1
        assert str(item.head_node_id) == out["node_id"]
        assert await _heads(twin, item) == [UUID(out["node_id"])]
        wp = await twin.get_work_product(UUID(out["node_id"]))
        assert wp.metadata["item_key"] == "CAD-SHELF-BRACKET"
        assert wp.metadata["item_revision"] == 1

    async def test_same_name_recommit_is_next_revision(self, twin) -> None:
        record = make_geometry_recorder(twin, None)
        v1 = await record(step_base64=_step("a"), name="Shelf Bracket", project_id=PROJECT)
        v2 = await record(
            step_base64=_step("b"),
            name="Shelf Bracket",
            project_id=PROJECT,
            change_reason="thicker flange",
        )

        assert v2["item_key"] == v1["item_key"]
        assert v2["revision"] == 2
        assert v2["supersedes_node_id"] == v1["node_id"]
        item = await find_item(twin, v1["item_key"], PROJECT)
        assert item.head_revision == 2
        # Exactly one HEAD, on the new revision.
        assert await _heads(twin, item) == [UUID(v2["node_id"])]
        # SUPERSEDES was added once (by the recorder), not twice.
        sup = await twin.graph.get_edges(
            UUID(v2["node_id"]), direction="outgoing", edge_type=EdgeType.SUPERSEDES
        )
        assert [e.target_id for e in sup] == [UUID(v1["node_id"])]
        history = await item_history(twin, item)
        assert [r.revision for r in history] == [1, 2]
        assert history[1].change_reason == "thicker flange"
        assert [r.is_head for r in history] == [False, True]

    async def test_slug_equal_name_is_the_same_item(self, twin) -> None:
        record = make_geometry_recorder(twin, None)
        v1 = await record(step_base64=_step("a"), name="Shelf Bracket", project_id=PROJECT)
        v2 = await record(step_base64=_step("b"), name="shelf-bracket", project_id=PROJECT)
        assert (v2["item_key"], v2["revision"]) == (v1["item_key"], 2)

    async def test_drifted_name_without_key_is_a_new_item(self, twin) -> None:
        record = make_geometry_recorder(twin, None)
        v1 = await record(step_base64=_step("a"), name="Shelf Bracket", project_id=PROJECT)
        v2 = await record(step_base64=_step("b"), name="Wall Bracket", project_id=PROJECT)
        assert v2["item_key"] != v1["item_key"]
        assert v2["revision"] == 1

    async def test_drifted_name_with_item_key_revises_the_item(self, twin) -> None:
        record = make_geometry_recorder(twin, None)
        v1 = await record(step_base64=_step("a"), name="Shelf Bracket", project_id=PROJECT)
        v2 = await record(
            step_base64=_step("b"),
            name="Wall Bracket (rev B)",
            project_id=PROJECT,
            item_key=v1["item_key"],
        )
        assert (v2["item_key"], v2["revision"]) == (v1["item_key"], 2)
        item = await find_item(twin, v1["item_key"], PROJECT)
        assert item.name == "Wall Bracket (rev B)"
        # And the old name still finds the same item afterwards (key match).
        v3 = await record(step_base64=_step("c"), name="Shelf Bracket", project_id=PROJECT)
        assert (v3["item_key"], v3["revision"]) == (v1["item_key"], 3)

    async def test_drifted_name_with_supersedes_revises_the_item(self, twin) -> None:
        record = make_geometry_recorder(twin, None)
        v1 = await record(step_base64=_step("a"), name="Shelf Bracket", project_id=PROJECT)
        v2 = await record(
            step_base64=_step("b"),
            name="Bracket, wall side",
            project_id=PROJECT,
            supersedes=v1["node_id"],
        )
        assert (v2["item_key"], v2["revision"]) == (v1["item_key"], 2)

    async def test_stale_pinned_key_is_refused_before_any_write(self, twin) -> None:
        record = make_geometry_recorder(twin, None)
        v1 = await record(step_base64=_step("a"), name="Shelf Bracket", project_id=PROJECT)
        await record(step_base64=_step("b"), name="Shelf Bracket", project_id=PROJECT)
        before = len(await twin.list_work_products(project_id=UUID(PROJECT)))
        with pytest.raises(ItemRevisionConflictError, match="@2"):
            await record(
                step_base64=_step("c"),
                name="Shelf Bracket",
                project_id=PROJECT,
                item_key=f"{v1['item_key']}@1",
            )
        assert len(await twin.list_work_products(project_id=UUID(PROJECT))) == before

    async def test_unknown_pinned_key_is_an_error(self, twin) -> None:
        record = make_geometry_recorder(twin, None)
        with pytest.raises(UnknownItemError):
            await record(
                step_base64=_step("a"), name="X", project_id=PROJECT, item_key="CAD-NOPE@2"
            )

    async def test_unknown_bare_key_names_a_new_item(self, twin) -> None:
        record = make_geometry_recorder(twin, None)
        out = await record(step_base64=_step("a"), name="X", project_id=PROJECT, item_key="leg-a")
        assert (out["item_key"], out["revision"]) == ("LEG-A", 1)

    async def test_supersedes_unknown_node_is_an_error(self, twin) -> None:
        record = make_geometry_recorder(twin, None)
        with pytest.raises(UnknownItemError):
            await record(
                step_base64=_step("a"), name="X", project_id=PROJECT, supersedes=str(uuid4())
            )

    async def test_key_for_another_type_is_refused(self, twin) -> None:
        record_cs = make_constraint_recorder(twin, None)
        cs = await record_cs(
            title="Shelf reqs",
            constraints=[{"name": "load", "expression": "True"}],
            project_id=PROJECT,
        )
        record = make_geometry_recorder(twin, None)
        with pytest.raises(ItemError, match="constraint_set item"):
            await record(
                step_base64=_step("a"), name="X", project_id=PROJECT, item_key=cs["item_key"]
            )

    async def test_identical_recommit_reports_the_head(self, twin) -> None:
        record = make_geometry_recorder(twin, None)
        v1 = await record(step_base64=_step("a"), name="Shelf Bracket", project_id=PROJECT)
        again = await record(step_base64=_step("a"), name="Shelf Bracket", project_id=PROJECT)
        assert again["already_committed"] is True
        assert (again["item_key"], again["revision"]) == (v1["item_key"], 1)

    async def test_assembly_gets_an_assembly_item(self, twin) -> None:
        record = make_geometry_recorder(twin, None)
        part = await record(step_base64=_step("a"), name="Leg", project_id=PROJECT)
        asm = await record(
            step_base64=_step("asm"),
            name="Shelf",
            project_id=PROJECT,
            parts=[{"node_id": part["node_id"]}],
        )
        assert asm["item_key"] == "ASM-SHELF"
        item = await find_item(twin, "ASM-SHELF", PROJECT)
        assert item.item_type == "assembly"

    async def test_unscoped_commit_gets_no_item(self, twin) -> None:
        record = make_geometry_recorder(twin, None)
        out = await record(step_base64=_step("a"), name="Loose part")
        assert "item_key" not in out
        assert await list_items(twin) == []

    async def test_legacy_supersedes_chain_is_adopted(self, twin) -> None:
        """Nodes written before items existed join the item as @1..@k, unedited."""
        pid = UUID(PROJECT)
        old = []
        for i in range(2):
            wp = await twin.create_work_product(
                WorkProduct(
                    name="Upper Arm Link",
                    type=WorkProductType.CAD_MODEL,
                    domain="mechanical",
                    file_path="",
                    content_hash=f"legacy-{i}",
                    format="step",
                    created_by="legacy",
                    project_id=pid,
                )
            )
            if old:
                await twin.add_edge(wp.id, old[-1].id, EdgeType.SUPERSEDES)
            old.append(wp)

        record = make_geometry_recorder(twin, None)
        out = await record(step_base64=_step("new"), name="Upper Arm Link", project_id=PROJECT)

        assert out["revision"] == 3
        assert out["supersedes_node_id"] == str(old[-1].id)
        item = await find_item(twin, out["item_key"], PROJECT)
        history = await item_history(twin, item)
        assert [(r.revision, r.node_id, r.adopted) for r in history] == [
            (1, old[0].id, True),
            (2, old[1].id, True),
            (3, UUID(out["node_id"]), False),
        ]
        # Adopted nodes are not edited: no item stamp in their metadata.
        assert "item_key" not in (await twin.get_work_product(old[0].id)).metadata

    async def test_run_id_and_author_come_from_the_call_context(self, twin) -> None:
        from mcp_core.context import McpCallContext, with_context

        record = make_geometry_recorder(twin, None)
        ctx = McpCallContext(actor_id="agent:mechanical", run_id="run-42")
        with with_context(ctx):
            out = await record(step_base64=_step("a"), name="Leg", project_id=PROJECT)
        item = await find_item(twin, out["item_key"], PROJECT)
        (rev,) = await item_history(twin, item)
        assert (rev.run_id, rev.author) == ("run-42", "agent:mechanical")
        wp = await twin.get_work_product(UUID(out["node_id"]))
        assert wp.metadata["run_id"] == "run-42"

    async def test_item_link_failure_does_not_fail_the_commit(
        self, twin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        real_add_node = twin.graph.add_node

        async def boom(node: object) -> object:
            if isinstance(node, Item):
                raise RuntimeError("graph hiccup")
            return await real_add_node(node)

        record = make_geometry_recorder(twin, None)
        monkeypatch.setattr(twin.graph, "add_node", boom)
        out = await record(step_base64=_step("a"), name="Leg", project_id=PROJECT)
        assert out["node_id"]
        assert "item_key" not in out
        assert "item_warning" in out


class TestOtherDefinitionWrites:
    async def test_constraint_set_rerecord_is_next_revision(self, twin) -> None:
        record = make_constraint_recorder(twin, None)
        c = [{"name": "load", "expression": "True"}]
        v1 = await record(title="Shelf requirements", constraints=c, project_id=PROJECT)
        v2 = await record(title="Shelf requirements", constraints=c, project_id=PROJECT)
        assert v1["item_key"] == "CS-SHELF-REQUIREMENTS"
        assert (v2["item_key"], v2["revision"]) == (v1["item_key"], 2)
        sup = await twin.graph.get_edges(
            UUID(v2["node_id"]), direction="outgoing", edge_type=EdgeType.SUPERSEDES
        )
        assert [e.target_id for e in sup] == [UUID(v1["node_id"])]

    async def test_intent_is_a_definition(self, twin) -> None:
        record = make_engineering_entity_recorder(twin, None)
        v1 = await record(
            entity_type="intent", statement="A wall shelf", title="Shelf", project_id=PROJECT
        )
        v2 = await record(
            entity_type="intent",
            statement="A wall shelf holding 20 kg",
            title="Shelf",
            project_id=PROJECT,
        )
        assert v1["item_key"] == "INT-SHELF"
        assert v2["revision"] == 2
        entity = await twin.get_engineering_entity(UUID(v2["node_id"]))
        assert entity.metadata["item_revision"] == 2

    async def test_record_type_entity_gets_no_item(self, twin) -> None:
        record = make_engineering_entity_recorder(twin, None)
        out = await record(
            entity_type="assumption", statement="Studs at 400 mm", project_id=PROJECT
        )
        assert "item_key" not in out

    async def test_bom_rerecord_is_next_revision(self, twin) -> None:
        record = make_bom_recorder(twin, None)
        rows = [{"ref": "U1", "part": "ESP32", "qty": 1}]
        v1 = await record(rows=rows, name="Main BOM", project_id=PROJECT)
        v2 = await record(
            rows=rows + [{"ref": "R1", "part": "10k"}], name="Main BOM", project_id=PROJECT
        )
        assert (v1["item_key"], v2["revision"]) == ("BOM-MAIN-BOM", 2)

    async def test_component_selection_keyed_by_role(self, twin) -> None:
        record = make_component_recorder(twin, None)
        common = {"category": "mcu", "purchase_unit": "discrete_part", "project_id": PROJECT}
        v1 = await record(mpn="ESP32-S3", manufacturer="Espressif", role="main MCU", **common)
        v2 = await record(mpn="RP2040", manufacturer="Raspberry Pi", role="main MCU", **common)
        assert v1["item_key"] == "CMP-MAIN-MCU"
        assert (v2["item_key"], v2["revision"]) == (v1["item_key"], 2)


class TestReads:
    async def test_resolve_bare_key_is_head_and_pinned_is_that_revision(self, twin) -> None:
        record = make_geometry_recorder(twin, None)
        v1 = await record(step_base64=_step("a"), name="Leg", project_id=PROJECT)
        v2 = await record(step_base64=_step("b"), name="Leg", project_id=PROJECT)
        _, head = await resolve_item_ref(twin, "CAD-LEG")
        assert str(head.node_id) == v2["node_id"]
        _, first = await resolve_item_ref(twin, "CAD-LEG@1", PROJECT)
        assert str(first.node_id) == v1["node_id"]
        with pytest.raises(UnknownItemError):
            await resolve_item_ref(twin, "CAD-LEG@9")

    async def test_history_reader_by_key_and_by_node(self, twin) -> None:
        record = make_geometry_recorder(twin, None)
        v1 = await record(step_base64=_step("a"), name="Leg", project_id=PROJECT)
        v2 = await record(step_base64=_step("b"), name="Leg", project_id=PROJECT)
        read = make_item_history_reader(twin)
        by_key = await read(item_key="CAD-LEG")
        by_node = await read(node_id=v1["node_id"])
        assert by_key == by_node
        assert by_key["item"]["head_ref"] == "CAD-LEG@2"
        assert [r["node_id"] for r in by_key["revisions"]] == [v1["node_id"], v2["node_id"]]

    async def test_history_reader_errors(self, twin) -> None:
        read = make_item_history_reader(twin)
        with pytest.raises(UnknownItemError):
            await read(item_key="CAD-NOTHING")
        with pytest.raises(ValueError):
            await read()

    async def test_list_items_scoped_and_typed(self, twin) -> None:
        geo = make_geometry_recorder(twin, None)
        await geo(step_base64=_step("a"), name="Leg", project_id=PROJECT)
        cs = make_constraint_recorder(twin, None)
        await cs(
            title="Reqs", constraints=[{"name": "n", "expression": "True"}], project_id=PROJECT
        )
        other = str(uuid4())
        await geo(step_base64=_step("z"), name="Leg", project_id=other)

        assert [i.key for i in await list_items(twin, project_id=PROJECT)] == ["CAD-LEG", "CS-REQS"]
        assert [i.key for i in await list_items(twin, item_type="cad_model")] == [
            "CAD-LEG",
            "CAD-LEG",
        ]

    async def test_orphan_scan_ignores_item_edges(self, twin) -> None:
        record = make_component_recorder(twin, None)
        out = await record(
            mpn="X1",
            manufacturer="M",
            category="c",
            purchase_unit="discrete_part",
            item_key="CMP-X",
        )
        report = await twin.find_orphans()
        assert UUID(out["node_id"]) in report.orphan_bom_items


class TestNeo4jRoundTrip:
    def test_item_survives_serialisation(self) -> None:
        from twin_core.neo4j_graph_engine import Neo4jGraphEngine

        item = Item(
            key="CAD-LEG",
            item_type="cad_model",
            name="Leg",
            project_id=UUID(PROJECT),
            head_revision=3,
            head_node_id=uuid4(),
        )
        back = Neo4jGraphEngine._props_to_node(Neo4jGraphEngine._node_to_props(item))
        assert isinstance(back, Item)
        assert back == item
