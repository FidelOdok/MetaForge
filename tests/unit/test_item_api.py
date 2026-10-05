"""twin.item_history MCP tool, item args on write tools, and the /v1/twin/items routes
(FORGE-523)."""

from __future__ import annotations

import base64

import pytest
from httpx import ASGITransport, AsyncClient

from api_gateway.twin.geometry_recorder import make_geometry_recorder
from api_gateway.twin.item_revisions import make_item_history_reader
from tool_registry.tools.twin.adapter import TwinServer
from twin_core.api import InMemoryTwinAPI

PROJECT = "66666666-6666-6666-6666-666666666666"
_STEP_A = base64.b64encode(b"ISO-10303-21;\na\nENDSEC;\n").decode("ascii")
_STEP_B = base64.b64encode(b"ISO-10303-21;\nb\nENDSEC;\n").decode("ascii")


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


class TestMcpSurface:
    def test_item_history_registered_only_with_reader(self, twin) -> None:
        assert "twin.item_history" not in TwinServer(twin=twin).tool_ids
        server = TwinServer(twin=twin, item_history_reader=make_item_history_reader(twin))
        assert "twin.item_history" in server.tool_ids

    def test_write_tools_advertise_optional_item_args(self, twin) -> None:
        server = TwinServer(twin=twin, geometry_recorder=make_geometry_recorder(twin, None))
        manifest = server._tools["twin.commit_geometry"].manifest
        props = manifest.input_schema["properties"]
        for name in ("item_key", "supersedes", "change_reason"):
            assert name in props
            assert name not in manifest.input_schema.get("required", [])
        assert {"item_key", "revision"} <= set(manifest.output_schema["properties"])

    async def test_commit_geometry_item_key_and_history_round_trip(self, twin) -> None:
        server = TwinServer(
            twin=twin,
            geometry_recorder=make_geometry_recorder(twin, None),
            item_history_reader=make_item_history_reader(twin),
        )
        v1 = await server.commit_geometry(
            {"name": "Leg", "step_base64": _STEP_A, "project_id": PROJECT}
        )
        v2 = await server.commit_geometry(
            {
                "name": "Leg, front left",
                "step_base64": _STEP_B,
                "project_id": PROJECT,
                "item_key": v1["item_key"],
                "change_reason": "renamed and lengthened",
            }
        )
        assert (v2["item_key"], v2["revision"]) == ("CAD-LEG", 2)

        history = await server.item_history({"item_key": "CAD-LEG"})
        assert history["item"]["head_revision"] == 2
        assert [r["revision"] for r in history["revisions"]] == [1, 2]
        assert history["revisions"][1]["change_reason"] == "renamed and lengthened"

    async def test_null_item_args_are_accepted_and_ignored(self, twin) -> None:
        # Live: a model sent change_reason=null and validation refused the
        # whole record_constraint_set call. Null must mean "not given".
        jsonschema = pytest.importorskip("jsonschema")
        server = TwinServer(twin=twin, geometry_recorder=make_geometry_recorder(twin, None))
        schema = server._tools["twin.commit_geometry"].manifest.input_schema
        nulls = {"item_key": None, "supersedes": None, "change_reason": None}
        args = {"name": "Leg", "step_base64": _STEP_A, "project_id": PROJECT, **nulls}
        jsonschema.validate(args, schema)

        v1 = await server.commit_geometry(args)
        v2 = await server.commit_geometry({**args, "step_base64": _STEP_B})
        assert (v1["revision"], v2["revision"]) == (1, 2)
        assert v1["item_key"] == v2["item_key"]

    async def test_item_history_needs_a_reference(self, twin) -> None:
        server = TwinServer(twin=twin, item_history_reader=make_item_history_reader(twin))
        with pytest.raises(ValueError, match="item_key"):
            await server.item_history({})

    def test_item_history_is_annotated_read_only(self) -> None:
        from mcp_core.annotations import READ_ONLY

        assert "twin.item_history" in READ_ONLY

    def test_bootstrap_accepts_the_reader(self) -> None:
        """A new TwinServer collaborator must be threaded through bootstrap too."""
        import inspect

        from tool_registry.bootstrap import bootstrap_tool_registry

        assert "item_history_reader" in inspect.signature(bootstrap_tool_registry).parameters


class TestRoutes:
    @pytest.fixture
    def client(self, twin):
        from fastapi import FastAPI

        from api_gateway.twin import routes
        from api_gateway.twin.item_routes import router

        previous = routes.get_twin()
        routes.init_twin(twin)
        app = FastAPI()
        app.include_router(router)
        yield AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        routes.init_twin(previous)

    async def test_list_items_and_revisions(self, twin, client) -> None:
        record = make_geometry_recorder(twin, None)
        await record(step_base64=_STEP_A, name="Leg", project_id=PROJECT)
        await record(step_base64=_STEP_B, name="Leg", project_id=PROJECT)

        resp = await client.get("/v1/twin/items", params={"project_id": PROJECT})
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["key"] == "CAD-LEG"
        assert body["items"][0]["head_ref"] == "CAD-LEG@2"

        resp = await client.get("/v1/twin/items/CAD-LEG/revisions")
        assert resp.status_code == 200
        revisions = resp.json()["revisions"]
        assert [r["revision"] for r in revisions] == [1, 2]
        assert [r["is_head"] for r in revisions] == [False, True]

    async def test_unknown_key_is_404_and_bad_project_400(self, client) -> None:
        assert (await client.get("/v1/twin/items/CAD-NOPE/revisions")).status_code == 404
        assert (await client.get("/v1/twin/items", params={"project_id": "x"})).status_code == 400

    async def test_relationships_hide_item_edges(self, twin) -> None:
        from api_gateway.twin import routes

        record = make_geometry_recorder(twin, None)
        await record(step_base64=_STEP_A, name="Leg", project_id=PROJECT)
        await record(step_base64=_STEP_B, name="Leg", project_id=PROJECT)
        previous = routes.get_twin()
        routes.init_twin(twin)
        try:
            result = await routes.list_twin_relationships(project_id=PROJECT)
        finally:
            routes.init_twin(previous)
        types = {r.type for r in result.relationships}
        assert "supersedes" in types
        assert not types & {"revision_of", "head"}
