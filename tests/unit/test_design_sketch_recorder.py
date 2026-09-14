"""Unit tests for make_design_sketch_recorder / _approver (follow-up to
MET-740/747).

Exercises the design-sketch persistence facets (single HTML blob -> MinIO,
design_sketch work product with an unapproved-by-default approval gate,
PARENT_OF edges to whatever existing work products the sketch reviews,
project link, and the approve state-transition/versioning path) with a
fake/real twin + project backend and the MinIO blob store monkeypatched, so
no real storage is required.
"""

from __future__ import annotations

from uuid import UUID

import pytest

from api_gateway.twin.design_sketch_recorder import (
    make_design_sketch_approver,
    make_design_sketch_recorder,
)

_SKETCH_HTML = "<h1>Leg revision sketch</h1><p>thigh 50mm, shin 50mm</p>"


class _FakeTwin:
    def __init__(self) -> None:
        self.created: list = []
        self.edges: list = []

    async def create_work_product(self, wp):  # type: ignore[no-untyped-def]
        self.created.append(wp)
        return wp

    async def add_edge(self, source_id, target_id, edge_type, metadata=None):  # type: ignore[no-untyped-def]
        self.edges.append((source_id, target_id, edge_type))


class _FakeProjectBackend:
    def __init__(self) -> None:
        self.links: list = []

    async def link_work_product(self, project_id, node_id, name, kind):  # type: ignore[no-untyped-def]
        self.links.append((project_id, node_id, name, kind))


@pytest.fixture()
def patched_blob_store(monkeypatch: pytest.MonkeyPatch) -> dict:
    captured: dict = {"calls": []}

    def fake_store(node_id: str, filename: str, content: bytes, content_type: str = "") -> str:
        key = f"work-products/{node_id}/{filename}"
        captured["calls"].append(
            {"node_id": node_id, "filename": filename, "content": content, "key": key}
        )
        return key

    import digital_twin.storage.work_product_blobs as blobs

    monkeypatch.setattr(blobs, "store_work_product_blob", fake_store)
    return captured


class TestDesignSketchCommit:
    async def test_persists_unapproved_sketch(self, patched_blob_store: dict) -> None:
        from twin_core.models.enums import WorkProductType

        twin = _FakeTwin()
        projects = _FakeProjectBackend()
        commit = make_design_sketch_recorder(twin, projects)

        project_id = "11111111-1111-1111-1111-111111111111"
        result = await commit(
            name="Quadruped Leg Revision",
            html_content=_SKETCH_HTML,
            description_text="checking thigh/shin proportions before CAD",
            project_id=project_id,
        )

        assert len(twin.created) == 1
        wp = twin.created[0]
        assert wp.type == WorkProductType.DESIGN_SKETCH
        assert wp.format == "html"
        assert wp.metadata["approved"] is False
        assert wp.metadata["approved_at"] is None
        assert wp.metadata["description_text"] == "checking thigh/shin proportions before CAD"
        assert wp.metadata["minio_object_key"] == result["minio_object_key"]
        assert wp.file_path == ""

        assert patched_blob_store["calls"][0]["filename"] == "quadruped-leg-revision.html"
        assert projects.links == [
            (project_id, result["node_id"], "Quadruped Leg Revision", "design_sketch")
        ]
        assert result["project_linked"] is True

    async def test_source_node_ids_create_parent_of_edges(self, patched_blob_store: dict) -> None:
        twin = _FakeTwin()
        commit = make_design_sketch_recorder(twin, None)
        result = await commit(
            name="Revision sketch",
            html_content=_SKETCH_HTML,
            source_node_ids=["aaaaaaaa-0000-0000-0000-000000000001"],
        )
        assert len(twin.edges) == 1
        assert twin.edges[0][1] == "aaaaaaaa-0000-0000-0000-000000000001"
        assert result["node_id"]

    async def test_no_source_node_ids_creates_no_edges(self, patched_blob_store: dict) -> None:
        """A brand-new design has nothing built yet to link to."""
        twin = _FakeTwin()
        commit = make_design_sketch_recorder(twin, None)
        await commit(name="New design sketch", html_content=_SKETCH_HTML)
        assert twin.edges == []

    async def test_degrades_when_blob_store_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import digital_twin.storage.work_product_blobs as blobs

        def boom(*a: object, **k: object) -> str:
            raise RuntimeError("minio down")

        monkeypatch.setattr(blobs, "store_work_product_blob", boom)
        twin = _FakeTwin()
        commit = make_design_sketch_recorder(twin, None)
        result = await commit(name="Sketch", html_content=_SKETCH_HTML)
        assert len(twin.created) == 1
        assert result["minio_object_key"] is None
        assert result["project_linked"] is False

    async def test_bad_source_edge_does_not_block_commit(self, patched_blob_store: dict) -> None:
        class _EdgeFailTwin(_FakeTwin):
            async def add_edge(self, source_id, target_id, edge_type, metadata=None):  # type: ignore[no-untyped-def]
                raise RuntimeError("bad node id")

        twin = _EdgeFailTwin()
        commit = make_design_sketch_recorder(twin, None)
        result = await commit(name="Sketch", html_content=_SKETCH_HTML, source_node_ids=["bad-id"])
        assert result["node_id"]

    async def test_missing_name_raises(self) -> None:
        commit = make_design_sketch_recorder(_FakeTwin(), None)
        with pytest.raises(ValueError, match="name"):
            await commit(name="", html_content=_SKETCH_HTML)

    async def test_empty_html_raises(self) -> None:
        commit = make_design_sketch_recorder(_FakeTwin(), None)
        with pytest.raises(ValueError, match="empty"):
            await commit(name="Sketch", html_content="")


class TestDesignSketchApprove:
    async def test_approve_flips_gate_and_records_version(self, patched_blob_store: dict) -> None:
        from twin_core.api import InMemoryTwinAPI

        twin = InMemoryTwinAPI.create()
        commit = make_design_sketch_recorder(twin, None)
        approve = make_design_sketch_approver(twin)

        created = await commit(name="Leg revision", html_content=_SKETCH_HTML)
        node_id = created["node_id"]

        result = await approve(node_id, approved_by="fidel")

        assert result["approved"] is True
        assert result["approved_at"]

        wp = await twin.get_work_product(UUID(node_id))
        assert wp.metadata["approved"] is True
        assert wp.metadata["approved_by"] == "fidel"
        revisions = wp.metadata["_revisions"]
        assert len(revisions) == 1
        assert revisions[0]["metadata_snapshot"]["approved"] is True

    async def test_approve_missing_node_raises(self) -> None:
        from twin_core.api import InMemoryTwinAPI

        twin = InMemoryTwinAPI.create()
        approve = make_design_sketch_approver(twin)
        with pytest.raises(ValueError, match="not found"):
            await approve("11111111-1111-1111-1111-111111111111")

    async def test_double_approve_raises(self, patched_blob_store: dict) -> None:
        from twin_core.api import InMemoryTwinAPI

        twin = InMemoryTwinAPI.create()
        commit = make_design_sketch_recorder(twin, None)
        approve = make_design_sketch_approver(twin)
        created = await commit(name="Sketch", html_content=_SKETCH_HTML)

        await approve(created["node_id"])
        with pytest.raises(ValueError, match="already approved"):
            await approve(created["node_id"])


class TestCommitDesignSketchAdapter:
    """twin.commit_design_sketch tool — registration + handler against InMemoryTwinAPI."""

    @staticmethod
    def _patch_blobs(monkeypatch: pytest.MonkeyPatch) -> None:
        import digital_twin.storage.work_product_blobs as blobs

        monkeypatch.setattr(
            blobs,
            "store_work_product_blob",
            lambda nid, fn, content, content_type="": f"work-products/{nid}/{fn}",
        )

    async def test_tool_registered_and_persists(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from uuid import UUID

        from tool_registry.tools.twin.adapter import TwinServer
        from twin_core.api import InMemoryTwinAPI
        from twin_core.models.enums import WorkProductType

        self._patch_blobs(monkeypatch)
        twin = InMemoryTwinAPI.create()
        server = TwinServer(
            twin=twin, design_sketch_recorder=make_design_sketch_recorder(twin, None)
        )
        assert "twin.commit_design_sketch" in server.tool_ids

        out = await server.commit_design_sketch(
            {"name": "Leg Revision", "html_content": _SKETCH_HTML}
        )
        wp = await twin.get_work_product(UUID(out["node_id"]))
        assert wp is not None
        assert wp.type == WorkProductType.DESIGN_SKETCH
        assert wp.metadata["approved"] is False

    def test_tool_absent_without_recorder(self) -> None:
        from tool_registry.tools.twin.adapter import TwinServer
        from twin_core.api import InMemoryTwinAPI

        server = TwinServer(twin=InMemoryTwinAPI.create())
        assert "twin.commit_design_sketch" not in server.tool_ids

    async def test_handler_validates_required_fields(self) -> None:
        from tool_registry.tools.twin.adapter import TwinServer
        from twin_core.api import InMemoryTwinAPI

        twin = InMemoryTwinAPI.create()
        server = TwinServer(
            twin=twin, design_sketch_recorder=make_design_sketch_recorder(twin, None)
        )
        with pytest.raises(ValueError, match="name"):
            await server.commit_design_sketch({"html_content": _SKETCH_HTML})
        with pytest.raises(ValueError, match="html_content"):
            await server.commit_design_sketch({"name": "x"})

    async def test_handler_forwards_optional_fields(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tool_registry.tools.twin.adapter import TwinServer
        from twin_core.api import InMemoryTwinAPI

        self._patch_blobs(monkeypatch)
        twin = InMemoryTwinAPI.create()
        server = TwinServer(
            twin=twin, design_sketch_recorder=make_design_sketch_recorder(twin, None)
        )
        out = await server.commit_design_sketch(
            {
                "name": "Leg Revision",
                "html_content": _SKETCH_HTML,
                "description_text": "checking proportions",
                "domain": "mechanical",
                "source_tool": "mechanical.decide_sketch_needed",
            }
        )
        from uuid import UUID

        wp = await twin.get_work_product(UUID(out["node_id"]))
        assert wp is not None
        assert wp.created_by == "mechanical.decide_sketch_needed"
        assert wp.metadata["description_text"] == "checking proportions"


class TestApproveSketchRoute:
    """POST /v1/twin/nodes/{id}/approve-sketch (follow-up to MET-740/747)."""

    @pytest.fixture(autouse=True)
    def _wire_approver(self, monkeypatch: pytest.MonkeyPatch):
        import api_gateway.twin.routes as routes

        self._patch_blobs(monkeypatch)
        approver = make_design_sketch_approver(routes._twin)
        routes.init_design_sketch_approver(approver)
        yield
        routes.init_design_sketch_approver(None)

    @staticmethod
    def _patch_blobs(monkeypatch: pytest.MonkeyPatch) -> None:
        import digital_twin.storage.work_product_blobs as blobs

        monkeypatch.setattr(
            blobs,
            "store_work_product_blob",
            lambda nid, fn, content, content_type="": f"work-products/{nid}/{fn}",
        )

    @pytest.fixture
    def app(self):
        from fastapi import FastAPI

        from api_gateway.twin.routes import router

        app = FastAPI()
        app.include_router(router)
        return app

    @pytest.fixture
    def client(self, app):
        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=app)
        return AsyncClient(transport=transport, base_url="http://test")

    @pytest.fixture
    def twin(self):
        from api_gateway.twin.routes import _twin

        _twin._graph._nodes.clear()
        _twin._graph._outgoing.clear()
        _twin._graph._incoming.clear()
        return _twin

    async def _create_sketch(self, twin) -> str:
        commit = make_design_sketch_recorder(twin, None)
        result = await commit(name="Leg Revision", html_content=_SKETCH_HTML)
        return result["node_id"]

    async def test_approve_success(self, client, twin) -> None:
        node_id = await self._create_sketch(twin)
        async with client:
            resp = await client.post(f"/v1/twin/nodes/{node_id}/approve-sketch", json={})
        assert resp.status_code == 200
        body = resp.json()
        assert body["approved"] is True
        assert body["node_id"] == node_id

    async def test_approve_with_approved_by(self, client, twin) -> None:
        node_id = await self._create_sketch(twin)
        async with client:
            resp = await client.post(
                f"/v1/twin/nodes/{node_id}/approve-sketch", json={"approved_by": "fidel"}
            )
        assert resp.status_code == 200

        from uuid import UUID

        wp = await twin.get_work_product(UUID(node_id))
        assert wp is not None
        assert wp.metadata["approved_by"] == "fidel"

    async def test_approve_unknown_node_404(self, client, twin) -> None:
        async with client:
            resp = await client.post(
                "/v1/twin/nodes/00000000-0000-0000-0000-000000000099/approve-sketch", json={}
            )
        assert resp.status_code == 404

    async def test_approve_wrong_wp_type_400(self, client, twin) -> None:
        from api_gateway.twin.geometry_recorder import make_geometry_recorder

        record = make_geometry_recorder(twin, None)
        result = await record(
            step_base64="SVNPLTEwMzAzLTIxOwo=",
            name="Not a sketch",
        )
        async with client:
            resp = await client.post(f"/v1/twin/nodes/{result['node_id']}/approve-sketch", json={})
        assert resp.status_code == 400

    async def test_double_approve_409(self, client, twin) -> None:
        node_id = await self._create_sketch(twin)
        async with client:
            resp1 = await client.post(f"/v1/twin/nodes/{node_id}/approve-sketch", json={})
            assert resp1.status_code == 200
            resp2 = await client.post(f"/v1/twin/nodes/{node_id}/approve-sketch", json={})
        assert resp2.status_code == 409

    async def test_approver_not_configured_503(self, client, twin) -> None:
        import api_gateway.twin.routes as routes

        node_id = await self._create_sketch(twin)
        routes.init_design_sketch_approver(None)
        async with client:
            resp = await client.post(f"/v1/twin/nodes/{node_id}/approve-sketch", json={})
        assert resp.status_code == 503
