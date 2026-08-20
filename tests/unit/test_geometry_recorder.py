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


# --------------------------------------------------------------------------
# Unconstrained-project soft warning (MET-584)
# --------------------------------------------------------------------------


class _WarnBackend:
    """get_project returns a project whose WPs carry the given types."""

    def __init__(self, types: list[str] | None) -> None:
        self._types = types
        self.links: list = []

    async def link_work_product(self, *a) -> None:
        self.links.append(a)

    async def get_project(self, project_id: str):
        from types import SimpleNamespace

        if self._types is None:
            return None
        return SimpleNamespace(work_products=[SimpleNamespace(type=t) for t in self._types])


# --------------------------------------------------------------------------
# Script-as-SSOT + graph geometry features (MET-630)
# --------------------------------------------------------------------------


class TestScriptAndGeometryFeatures:
    async def test_parameters_and_properties_stored_on_node(self, patched_blob_store: dict) -> None:
        twin = _FakeTwin()
        record = make_geometry_recorder(twin, None)

        await record(
            step_base64=_STEP_B64,
            name="Bracket",
            parameters={"pad_length_mm": 15, "hole_diameter_mm": 6},
            properties={"volume_mm3": 12345.6, "bounding_box": [10, 20, 30]},
        )

        wp = twin.created[0]
        features = wp.metadata["geometry_features"]
        assert features["parameters"] == {"pad_length_mm": 15, "hole_diameter_mm": 6}
        assert features["properties"]["volume_mm3"] == 12345.6

    async def test_script_source_commits_to_git_and_links_provenance(
        self, patched_blob_store: dict, tmp_path
    ) -> None:
        from api_gateway.twin.git_repo_registry import GitRepoRegistry
        from twin_core.api import InMemoryTwinAPI
        from twin_core.models.enums import EdgeType, WorkProductType

        twin = InMemoryTwinAPI.create()
        registry = GitRepoRegistry(twin.graph, tmp_path / "repo")
        record = make_geometry_recorder(twin, None, registry)

        result = await record(
            step_base64=_STEP_B64,
            name="Bracket",
            project_id="11111111-1111-1111-1111-111111111111",
            script_source="pad(10)\n",
        )

        assert result["script_node_id"] is not None
        assert result["git_commit_sha"] is not None

        from uuid import UUID

        step_wp = await twin.get_work_product(UUID(result["node_id"]))
        assert step_wp.metadata["git_commit_sha"] == result["git_commit_sha"]
        assert step_wp.metadata["git_path"] == "mechanical/cad_src/bracket.py"

        script_wp = await twin.get_work_product(UUID(result["script_node_id"]))
        assert script_wp.type == WorkProductType.CAD_SOURCE_SCRIPT

        edges = await twin.graph.get_edges(
            UUID(result["script_node_id"]), direction="outgoing", edge_type=EdgeType.PARENT_OF
        )
        assert len(edges) == 1
        assert edges[0].target_id == step_wp.id

        # A second commit on the same project reuses the "main" branch
        # instead of raising on the already-exists branch.
        result2 = await record(
            step_base64=_STEP_B64,
            name="Bracket",
            project_id="11111111-1111-1111-1111-111111111111",
            script_source="pad(15)\n",
        )
        assert result2["git_commit_sha"] != result["git_commit_sha"]

    async def test_regenerating_same_name_links_supersedes_chain(
        self, patched_blob_store: dict, tmp_path
    ) -> None:
        from api_gateway.twin.git_repo_registry import GitRepoRegistry
        from twin_core.api import InMemoryTwinAPI
        from twin_core.models.enums import EdgeType

        twin = InMemoryTwinAPI.create()
        registry = GitRepoRegistry(twin.graph, tmp_path / "repo")
        record = make_geometry_recorder(twin, None, registry)
        project_id = "22222222-2222-2222-2222-222222222222"

        from uuid import UUID

        v1 = await record(
            step_base64=_STEP_B64,
            name="Bracket",
            project_id=project_id,
            script_source="pad(10)\n",
        )
        v2 = await record(
            step_base64=_STEP_B64,
            name="Bracket",
            project_id=project_id,
            script_source="pad(15)\n",
        )

        assert v2["supersedes_node_id"] == v1["node_id"]
        assert "supersedes_node_id" not in v1

        step_edges = await twin.graph.get_edges(
            UUID(v2["node_id"]), direction="outgoing", edge_type=EdgeType.SUPERSEDES
        )
        assert len(step_edges) == 1
        assert step_edges[0].target_id == UUID(v1["node_id"])

        script_edges = await twin.graph.get_edges(
            UUID(v2["script_node_id"]), direction="outgoing", edge_type=EdgeType.SUPERSEDES
        )
        assert len(script_edges) == 1
        assert script_edges[0].target_id == UUID(v1["script_node_id"])

        # A third generation supersedes the second, not the first — the
        # chain always points at the current tip, mirroring get_current_datasheet.
        v3 = await record(
            step_base64=_STEP_B64,
            name="Bracket",
            project_id=project_id,
            script_source="pad(20)\n",
        )
        assert v3["supersedes_node_id"] == v2["node_id"]

    async def test_different_name_does_not_link_supersedes(
        self, patched_blob_store: dict, tmp_path
    ) -> None:
        from api_gateway.twin.git_repo_registry import GitRepoRegistry
        from twin_core.api import InMemoryTwinAPI

        twin = InMemoryTwinAPI.create()
        registry = GitRepoRegistry(twin.graph, tmp_path / "repo")
        record = make_geometry_recorder(twin, None, registry)
        project_id = "33333333-3333-3333-3333-333333333333"

        await record(
            step_base64=_STEP_B64, name="Bracket", project_id=project_id, script_source="a\n"
        )
        v2 = await record(
            step_base64=_STEP_B64, name="Bolt", project_id=project_id, script_source="b\n"
        )
        assert "supersedes_node_id" not in v2

    async def test_no_project_id_never_links_supersedes(self, patched_blob_store: dict) -> None:
        twin = _FakeTwin()
        record = make_geometry_recorder(twin, None)

        await record(step_base64=_STEP_B64, name="Bracket")
        v2 = await record(step_base64=_STEP_B64, name="Bracket")
        assert "supersedes_node_id" not in v2

    async def test_script_commit_failure_does_not_block_step_node(
        self, patched_blob_store: dict
    ) -> None:
        class _BoomRegistry:
            def for_project(self, project_id):  # type: ignore[no-untyped-def]
                raise RuntimeError("git unavailable")

        twin = _FakeTwin()
        record = make_geometry_recorder(twin, None, _BoomRegistry())

        result = await record(step_base64=_STEP_B64, name="Bracket", script_source="pad(10)\n")

        assert len(twin.created) == 1
        assert "script_node_id" not in result


@pytest.mark.asyncio
async def test_unconstrained_warning_paths() -> None:
    from api_gateway.twin.geometry_recorder import _unconstrained_warning

    # No requirements recorded -> warn.
    w = await _unconstrained_warning(_WarnBackend(["cad_model"]), "p-1")
    assert w and "no recorded requirements" in w.lower()
    # constraint_set or prd present -> silent.
    assert await _unconstrained_warning(_WarnBackend(["constraint_set"]), "p-1") is None
    assert await _unconstrained_warning(_WarnBackend(["prd", "cad_model"]), "p-1") is None
    # Unscopable (no backend / no project / no project_id) -> silent, never raises.
    assert await _unconstrained_warning(None, "p-1") is None
    assert await _unconstrained_warning(_WarnBackend(None), "p-1") is None
    assert await _unconstrained_warning(_WarnBackend(["x"]), None) is None
