"""Unit tests for make_geometry_recorder (MET-529).

Exercises the authored-geometry persistence facets (blob → MinIO, CAD_MODEL
work product, project link) with a fake twin + project backend and the MinIO
blob store monkeypatched, so no real storage is required.
"""

from __future__ import annotations

import base64

import pytest

from api_gateway.twin.geometry_recorder import make_geometry_recorder

_STEP = b"ISO-10303-21;\nHEADER;\nfake step body\nENDSEC;\n"
_STEP_B64 = base64.b64encode(_STEP).decode("ascii")


class _FakeTwin:
    def __init__(self) -> None:
        self.created: list = []

    async def create_work_product(self, wp):  # type: ignore[no-untyped-def]
        self.created.append(wp)
        return wp


class _FakeProjectBackend:
    def __init__(self) -> None:
        self.links: list = []

    async def link_work_product(self, project_id, node_id, name, kind):  # type: ignore[no-untyped-def]
        self.links.append((project_id, node_id, name, kind))


@pytest.fixture()
def patched_blob_store(monkeypatch: pytest.MonkeyPatch) -> dict:
    captured: dict = {}

    def fake_store(node_id: str, filename: str, content: bytes, content_type: str = "") -> str:
        captured["node_id"] = node_id
        captured["filename"] = filename
        captured["content"] = content
        captured["content_type"] = content_type
        return f"work-products/{node_id}/{filename}"

    import digital_twin.storage.work_product_blobs as blobs

    monkeypatch.setattr(blobs, "store_work_product_blob", fake_store)
    return captured


class TestGeometryRecorder:
    async def test_persists_cad_model_with_minio_key(self, patched_blob_store: dict) -> None:
        from twin_core.models.enums import WorkProductType

        twin = _FakeTwin()
        projects = _FakeProjectBackend()
        record = make_geometry_recorder(twin, projects)

        project_id = "11111111-1111-1111-1111-111111111111"
        result = await record(
            step_base64=_STEP_B64,
            name="Drone Arm",
            project_id=project_id,
            session_id="sess-1",
        )

        # A CAD_MODEL work product was created with content_hash + minio key.
        assert len(twin.created) == 1
        wp = twin.created[0]
        assert wp.type == WorkProductType.CAD_MODEL
        assert wp.format == "step"
        assert wp.metadata["minio_object_key"] == result["minio_object_key"]
        assert wp.metadata["content_sha256"] == result["content_hash"]
        assert wp.metadata["session_id"] == "sess-1"
        assert wp.file_path == ""  # MinIO is the source of truth

        # Blob stored with a CAD content type + slugged filename.
        assert patched_blob_store["content"] == _STEP
        assert patched_blob_store["filename"] == "drone-arm.step"
        assert patched_blob_store["content_type"] == "application/step"

        # Project linked + a render URL returned for the viewer.
        assert projects.links == [(project_id, result["node_id"], "Drone Arm", "cad_model")]
        assert result["project_linked"] is True
        assert result["model_url"] == f"/v1/twin/nodes/{result['node_id']}/model"
        assert result["size_bytes"] == len(_STEP)

    async def test_degrades_when_blob_store_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # MinIO down → node still created, just without a minio_object_key.
        import digital_twin.storage.work_product_blobs as blobs

        def boom(*a: object, **k: object) -> str:
            raise RuntimeError("minio down")

        monkeypatch.setattr(blobs, "store_work_product_blob", boom)
        twin = _FakeTwin()
        record = make_geometry_recorder(twin, None)
        result = await record(step_base64=_STEP_B64, name="Part")
        assert len(twin.created) == 1
        assert result["minio_object_key"] is None
        assert result["project_linked"] is False  # no project_id / backend

    async def test_invalid_base64_raises(self) -> None:
        record = make_geometry_recorder(_FakeTwin(), None)
        with pytest.raises(ValueError, match="not valid base64"):
            await record(step_base64="!!!not base64!!!", name="Part")

    async def test_empty_geometry_raises(self) -> None:
        record = make_geometry_recorder(_FakeTwin(), None)
        with pytest.raises(ValueError, match="empty"):
            await record(step_base64="", name="Part")

    async def test_missing_name_raises(self) -> None:
        record = make_geometry_recorder(_FakeTwin(), None)
        with pytest.raises(ValueError, match="name"):
            await record(step_base64=_STEP_B64, name="")


class TestCommitGeometryAdapter:
    """twin.commit_geometry tool — registration + handler against InMemoryTwinAPI."""

    async def test_tool_registered_and_persists(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import digital_twin.storage.work_product_blobs as blobs

        monkeypatch.setattr(
            blobs,
            "store_work_product_blob",
            lambda nid, fn, content, content_type="": f"work-products/{nid}/{fn}",
        )
        from uuid import UUID

        from tool_registry.tools.twin.adapter import TwinServer
        from twin_core.api import InMemoryTwinAPI
        from twin_core.models.enums import WorkProductType

        twin = InMemoryTwinAPI.create()
        server = TwinServer(twin=twin, geometry_recorder=make_geometry_recorder(twin, None))
        assert "twin.commit_geometry" in server.tool_ids

        out = await server.commit_geometry({"step_base64": _STEP_B64, "name": "Bracket"})
        wp = await twin.get_work_product(UUID(out["node_id"]))
        assert wp is not None
        assert wp.type == WorkProductType.CAD_MODEL
        assert out["model_url"].endswith("/model")

    def test_tool_absent_without_recorder(self) -> None:
        from tool_registry.tools.twin.adapter import TwinServer
        from twin_core.api import InMemoryTwinAPI

        server = TwinServer(twin=InMemoryTwinAPI.create())
        assert "twin.commit_geometry" not in server.tool_ids

    async def test_handler_validates_required_fields(self) -> None:
        from tool_registry.tools.twin.adapter import TwinServer
        from twin_core.api import InMemoryTwinAPI

        twin = InMemoryTwinAPI.create()
        server = TwinServer(twin=twin, geometry_recorder=make_geometry_recorder(twin, None))
        with pytest.raises(ValueError, match="step_base64"):
            await server.commit_geometry({"name": "x"})
        with pytest.raises(ValueError, match="name"):
            await server.commit_geometry({"step_base64": _STEP_B64})
