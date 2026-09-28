"""Load-case + simulation-result work-product API (FORGE-278, FORGE-279)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from api_gateway.simulation.routes import (
    CreateLoadCaseRequest,
    NamedFacesRequest,
    _wp_to_load_case,
    _wp_to_simulation_result,
    create_load_case,
    init_twin,
    list_load_cases,
    list_named_faces,
    list_simulation_results,
)
from twin_core.api import InMemoryTwinAPI
from twin_core.models.enums import WorkProductType
from twin_core.models.work_product import WorkProduct


def _patch_blob(monkeypatch: pytest.MonkeyPatch) -> None:
    def _store(
        node_id: str, filename: str, content: bytes, *, content_type: str | None = None
    ) -> str:
        return f"work-products/{node_id}/{filename}"

    monkeypatch.setattr("digital_twin.storage.work_product_blobs.store_work_product_blob", _store)


class _FakeProjectBackend:
    def __init__(self) -> None:
        self.links: list[tuple[str, str, str, str]] = []

    async def link_work_product(
        self, project_id: str, wp_id: str, name: str, link_type: str
    ) -> None:
        self.links.append((project_id, wp_id, name, link_type))


def _patch_project_backend(monkeypatch: pytest.MonkeyPatch) -> _FakeProjectBackend:
    be = _FakeProjectBackend()
    monkeypatch.setattr("api_gateway.projects.routes.get_project_backend", lambda: be)
    return be


class TestMapping:
    def test_maps_metadata_to_dashboard_shape(self) -> None:
        wp = WorkProduct(
            name="Cantilever Static Load",
            type=WorkProductType.LOAD_CASE,
            domain="mechanical",
            file_path="",
            content_hash="deadbeef",
            format="json",
            created_by="test",
            metadata={
                "material": {"name": "steel"},
                "fixed_node_set": "Surface1",
                "load_node_set": "Surface2",
                "load_force_n": [0, 0, -100],
                "source_of_loads": "Requirement REQ-12",
            },
        )
        lc = _wp_to_load_case(wp)
        assert lc.name == "Cantilever Static Load"
        assert lc.material == {"name": "steel"}
        assert lc.fixedNodeSet == "Surface1"
        assert lc.loadNodeSet == "Surface2"
        assert lc.loadForceN == [0, 0, -100]
        assert lc.sourceOfLoads == "Requirement REQ-12"

    def test_missing_metadata_fields_default_none(self) -> None:
        wp = WorkProduct(
            name="Bare",
            type=WorkProductType.LOAD_CASE,
            domain="mechanical",
            file_path="",
            content_hash="x",
            format="json",
            created_by="test",
        )
        lc = _wp_to_load_case(wp)
        assert lc.material is None
        assert lc.fixedNodeSet is None
        assert lc.loadForceN is None


class TestListLoadCases:
    async def test_empty_returns_empty_not_error(self) -> None:
        init_twin(InMemoryTwinAPI.create())
        r = await list_load_cases()
        assert r.total == 0
        assert r.loadCases == []

    async def test_lists_created_load_cases(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_blob(monkeypatch)
        _patch_project_backend(monkeypatch)
        twin = InMemoryTwinAPI.create()
        init_twin(twin)
        pid = "f8240b2a-9e01-4b16-83eb-b24cfcd4a04f"

        await create_load_case(
            CreateLoadCaseRequest(
                name="Cantilever Static Load",
                projectId=pid,
                material={"name": "steel"},
                fixedNodeSet="Surface1",
                loadNodeSet="Surface2",
                loadForceN=[0.0, 0.0, -100.0],
            )
        )

        listed = await list_load_cases(project_id=pid)
        assert listed.total == 1
        assert listed.loadCases[0].name == "Cantilever Static Load"
        assert listed.loadCases[0].fixedNodeSet == "Surface1"
        assert listed.loadCases[0].projectId == pid

    async def test_scopes_by_project(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_blob(monkeypatch)
        _patch_project_backend(monkeypatch)
        twin = InMemoryTwinAPI.create()
        init_twin(twin)
        pid_a = "f8240b2a-9e01-4b16-83eb-b24cfcd4a04f"
        pid_b = "372a67e9-1853-4577-b909-a6c79be86da3"
        for pid in (pid_a, pid_b):
            await create_load_case(
                CreateLoadCaseRequest(
                    name=f"Case for {pid}",
                    projectId=pid,
                    material={"name": "steel"},
                    fixedNodeSet="Surface1",
                    loadNodeSet="Surface2",
                    loadForceN=[0.0, 0.0, -100.0],
                )
            )

        scoped = await list_load_cases(project_id=pid_a)
        assert scoped.total == 1
        assert scoped.loadCases[0].projectId == pid_a

        every = await list_load_cases()
        assert every.total == 2

    async def test_invalid_project_id_400(self) -> None:
        init_twin(InMemoryTwinAPI.create())
        with pytest.raises(HTTPException) as exc:
            await list_load_cases(project_id="not-a-uuid")
        assert exc.value.status_code == 400


class TestCreateLoadCase:
    async def test_creates_a_real_load_case_work_product(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_blob(monkeypatch)
        _patch_project_backend(monkeypatch)
        twin = InMemoryTwinAPI.create()
        init_twin(twin)

        resp = await create_load_case(
            CreateLoadCaseRequest(
                name="Cantilever Static Load",
                projectId="f8240b2a-9e01-4b16-83eb-b24cfcd4a04f",
                material={"name": "steel"},
                fixedNodeSet="Surface1",
                loadNodeSet="Surface2",
                loadForceN=[0.0, 0.0, -100.0],
                sourceOfLoads="Requirement REQ-12",
            )
        )
        wp = await twin.get_work_product(UUID(resp.id))
        assert wp is not None
        assert wp.type == WorkProductType.LOAD_CASE
        assert wp.domain == "mechanical"
        assert wp.metadata["source_of_loads"] == "Requirement REQ-12"

    async def test_links_project_so_it_shows_on_the_work_product_list(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_blob(monkeypatch)
        be = _patch_project_backend(monkeypatch)
        twin = InMemoryTwinAPI.create()
        init_twin(twin)
        pid = "f8240b2a-9e01-4b16-83eb-b24cfcd4a04f"

        await create_load_case(
            CreateLoadCaseRequest(
                name="Cantilever Static Load",
                projectId=pid,
                material={"name": "steel"},
                fixedNodeSet="Surface1",
                loadNodeSet="Surface2",
                loadForceN=[0.0, 0.0, -100.0],
            )
        )
        assert be.links and be.links[0][0] == pid
        assert be.links[0][3] == "load_case"

    async def test_links_to_the_source_part(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_blob(monkeypatch)
        _patch_project_backend(monkeypatch)
        twin = InMemoryTwinAPI.create()
        init_twin(twin)
        part = await twin.create_work_product(
            WorkProduct(
                name="Bracket",
                type=WorkProductType.CAD_MODEL,
                domain="mechanical",
                file_path="",
                content_hash="deadbeef",
                format="step",
                created_by="test",
            )
        )

        resp = await create_load_case(
            CreateLoadCaseRequest(
                name="Cantilever Static Load",
                projectId="f8240b2a-9e01-4b16-83eb-b24cfcd4a04f",
                material={"name": "steel"},
                fixedNodeSet="Surface1",
                loadNodeSet="Surface2",
                loadForceN=[0.0, 0.0, -100.0],
                sourcePartNodeIds=[str(part.id)],
            )
        )
        assert resp.id

    def test_rejects_missing_required_fields(self) -> None:
        with pytest.raises(ValidationError):
            CreateLoadCaseRequest(name="Bare", projectId="p")


class TestSimulationResultMapping:
    def test_maps_metadata_to_dashboard_shape(self) -> None:
        wp = WorkProduct(
            name="Bracket FEA Result",
            type=WorkProductType.SIMULATION_RESULT,
            domain="mechanical",
            file_path="",
            content_hash="deadbeef",
            format="json",
            created_by="test",
            metadata={
                "max_von_mises_mpa": 42.0,
                "max_displacement_mm": 0.8,
                "load_case": "static_1g",
                "mesh_stats": {"num_nodes": 12500, "num_elements": 48000},
            },
        )
        r = _wp_to_simulation_result(wp)
        assert r.name == "Bracket FEA Result"
        assert r.maxVonMisesMpa == 42.0
        assert r.maxDisplacementMm == 0.8
        assert r.loadCase == "static_1g"
        assert r.meshStats == {"num_nodes": 12500, "num_elements": 48000}

    def test_missing_metadata_fields_default_none(self) -> None:
        wp = WorkProduct(
            name="Bare",
            type=WorkProductType.SIMULATION_RESULT,
            domain="mechanical",
            file_path="",
            content_hash="x",
            format="json",
            created_by="test",
        )
        r = _wp_to_simulation_result(wp)
        assert r.maxVonMisesMpa is None
        assert r.maxDisplacementMm is None
        assert r.loadCase is None
        assert r.meshStats is None


class TestListSimulationResults:
    async def test_empty_returns_empty_not_error(self) -> None:
        init_twin(InMemoryTwinAPI.create())
        r = await list_simulation_results()
        assert r.total == 0
        assert r.results == []

    async def test_lists_created_results_newest_first(self) -> None:
        twin = InMemoryTwinAPI.create()
        init_twin(twin)
        pid = "f8240b2a-9e01-4b16-83eb-b24cfcd4a04f"

        # Explicit, well-separated timestamps -- real wall-clock timing
        # between two awaits is not a reliable enough gap to assert an order
        # on.
        await twin.create_work_product(
            WorkProduct(
                name="Rev 1 FEA",
                type=WorkProductType.SIMULATION_RESULT,
                domain="mechanical",
                file_path="",
                content_hash="a",
                format="json",
                created_by="test",
                project_id=UUID(pid),
                created_at=datetime(2026, 9, 20, 10, 0, tzinfo=UTC),
                metadata={"max_von_mises_mpa": 60.0, "load_case": "static_1g"},
            )
        )
        await twin.create_work_product(
            WorkProduct(
                name="Rev 2 FEA",
                type=WorkProductType.SIMULATION_RESULT,
                domain="mechanical",
                file_path="",
                content_hash="b",
                format="json",
                created_by="test",
                project_id=UUID(pid),
                created_at=datetime(2026, 9, 22, 10, 0, tzinfo=UTC),
                metadata={"max_von_mises_mpa": 45.0, "load_case": "static_1g"},
            )
        )

        listed = await list_simulation_results(project_id=pid)
        assert listed.total == 2
        # Newest first.
        assert listed.results[0].name == "Rev 2 FEA"
        assert listed.results[1].name == "Rev 1 FEA"

    async def test_scopes_by_project(self) -> None:
        twin = InMemoryTwinAPI.create()
        init_twin(twin)
        pid_a = "f8240b2a-9e01-4b16-83eb-b24cfcd4a04f"
        pid_b = "372a67e9-1853-4577-b909-a6c79be86da3"
        for pid in (pid_a, pid_b):
            await twin.create_work_product(
                WorkProduct(
                    name=f"FEA for {pid}",
                    type=WorkProductType.SIMULATION_RESULT,
                    domain="mechanical",
                    file_path="",
                    content_hash=pid,
                    format="json",
                    created_by="test",
                    project_id=UUID(pid),
                    metadata={"max_von_mises_mpa": 10.0},
                )
            )

        scoped = await list_simulation_results(project_id=pid_a)
        assert scoped.total == 1
        assert scoped.results[0].projectId == pid_a

        every = await list_simulation_results()
        assert every.total == 2

    async def test_invalid_project_id_400(self) -> None:
        init_twin(InMemoryTwinAPI.create())
        with pytest.raises(HTTPException) as exc:
            await list_simulation_results(project_id="not-a-uuid")
        assert exc.value.status_code == 400


class _FakeBridge:
    """Fake MCP bridge for the named-faces route (mirrors
    ``test_cad_export_routes.py``'s pattern)."""

    def __init__(self, *, faces: list[dict] | None = None, fail: bool = False) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.faces = faces if faces is not None else []
        self.fail = fail

    async def invoke(self, tool_id: str, params: dict, timeout: int | None = None) -> dict:
        self.calls.append((tool_id, params))
        if self.fail:
            raise RuntimeError("gmsh worker exploded")
        return {"mesh_file": params["mesh_file"], "faces": self.faces}


_SURFACE1 = {
    "name": "Surface1",
    "element_type": "CPS3",
    "num_elements": 2,
    "num_nodes": 4,
    "bbox_mm": {"min": [0.0, 0.0, 0.0], "max": [10.0, 10.0, 0.0]},
    "centroid_mm": [5.0, 5.0, 0.0],
    "area_mm2": 100.0,
    "normal": [0.0, 0.0, 1.0],
}


class TestListNamedFaces:
    async def test_returns_faces_in_camel_case(self, monkeypatch: pytest.MonkeyPatch) -> None:
        bridge = _FakeBridge(faces=[_SURFACE1])
        monkeypatch.setattr("api_gateway.chat.routes.get_mcp_bridge", lambda: bridge)

        resp = await list_named_faces(NamedFacesRequest(meshFile="/workspace/bracket.inp"))

        assert resp.meshFile == "/workspace/bracket.inp"
        assert len(resp.faces) == 1
        face = resp.faces[0]
        assert face.name == "Surface1"
        assert face.centroidMm == [5.0, 5.0, 0.0]
        assert face.normal == [0.0, 0.0, 1.0]
        assert face.areaMm2 == 100.0
        assert face.bboxMm == {"min": [0.0, 0.0, 0.0], "max": [10.0, 10.0, 0.0]}
        assert bridge.calls == [
            ("freecad.list_named_faces", {"mesh_file": "/workspace/bracket.inp"})
        ]

    async def test_empty_faces_returns_empty_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        bridge = _FakeBridge(faces=[])
        monkeypatch.setattr("api_gateway.chat.routes.get_mcp_bridge", lambda: bridge)

        resp = await list_named_faces(NamedFacesRequest(meshFile="/workspace/empty.inp"))
        assert resp.faces == []

    async def test_tool_failure_raises_502(self, monkeypatch: pytest.MonkeyPatch) -> None:
        bridge = _FakeBridge(fail=True)
        monkeypatch.setattr("api_gateway.chat.routes.get_mcp_bridge", lambda: bridge)

        with pytest.raises(HTTPException) as exc:
            await list_named_faces(NamedFacesRequest(meshFile="/workspace/bracket.inp"))
        assert exc.value.status_code == 502

    def test_rejects_empty_mesh_file(self) -> None:
        with pytest.raises(ValidationError):
            NamedFacesRequest(meshFile="")
