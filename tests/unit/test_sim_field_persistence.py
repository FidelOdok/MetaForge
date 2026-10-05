"""Simulation result fields persisted and served (FORGE-532).

The calculix side (``test_calculix_field_payload.py``) builds the gzipped
``metaforge.sim_field`` payload. This covers the rest of the path:

- the document recorder storing it as a second MinIO blob on the
  simulation_result node, with ``field_*`` metadata, the analysed geometry
  pinned (id, revision, content hash) and fixtures/loads derived;
- ``twin.record_document`` reading it by reference (``field_file``);
- ``GET /v1/simulation/results/{id}/field`` serving it gzip-encoded, and
  the 404 ``field not stored`` a pre-FORGE-532 result gets.
"""

from __future__ import annotations

import base64
import gzip
import json
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from api_gateway.simulation.routes import (
    _wp_to_simulation_result,
    get_simulation_result_field,
    init_twin,
    router,
)
from api_gateway.twin.document_recorder import decode_sim_field, make_document_recorder
from tool_registry.tools.calculix.field_payload import build_field_payload, parse_frd_model
from tool_registry.tools.twin.adapter import TwinServer
from twin_core.api import InMemoryTwinAPI
from twin_core.models.enums import EdgeType, WorkProductType
from twin_core.models.work_product import WorkProduct

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "calculix"

_MARKERS = [
    {"kind": "fixture", "label": "Surface1", "position": [0, 5, 5], "node_count": 4},
    {"kind": "load", "label": "Surface2", "position": [20, 5, 5], "vector": [0, 0, -100]},
]


def _field_bytes() -> bytes:
    model = parse_frd_model(str(FIXTURES / "two_hex_static.frd"))
    return build_field_payload(model, "static_stress", markers=_MARKERS).gz_bytes


class _BlobStore:
    """Fake MinIO: records puts, serves gets."""

    def __init__(self, *, fail_on: str | None = None) -> None:
        self.objects: dict[str, tuple[bytes, str | None]] = {}
        self.fail_on = fail_on

    def store(
        self, node_id: str, filename: str, content: bytes, *, content_type: str | None = None
    ) -> str:
        if self.fail_on and filename.endswith(self.fail_on):
            raise RuntimeError("minio down")
        key = f"work-products/{node_id}/{filename}"
        self.objects[key] = (content, content_type)
        return key

    def fetch(self, key: str) -> bytes:
        return self.objects[key][0]


@pytest.fixture
def blobs(monkeypatch: pytest.MonkeyPatch) -> _BlobStore:
    store = _BlobStore()
    monkeypatch.setattr(
        "digital_twin.storage.work_product_blobs.store_work_product_blob", store.store
    )
    monkeypatch.setattr("api_gateway.twin.blob_store.fetch_work_product_blob", store.fetch)
    return store


async def _geometry(twin: InMemoryTwinAPI) -> WorkProduct:
    return await twin.create_work_product(
        WorkProduct(
            name="Bracket",
            type=WorkProductType.CAD_MODEL,
            domain="mechanical",
            file_path="",
            content_hash="geomhash123",
            format="step",
            created_by="test",
            metadata={"revision": "B"},
        )
    )


async def _record(twin: InMemoryTwinAPI, **kwargs: Any) -> dict[str, Any]:
    record = make_document_recorder(twin, None)
    return await record(
        content=json.dumps({"max_von_mises_mpa": 100.0}),
        name="Bracket FEA",
        wp_type="simulation_result",
        domain="mechanical",
        fmt="json",
        link_type="simulation_result",
        source_tool="twin.record_document",
        extra_metadata={"max_von_mises_mpa": 100.0, "load_case": "static_1g"},
        source_edge_type="derives_from",
        **kwargs,
    )


class TestRecorderStoresField:
    async def test_field_blob_stored_beside_the_summary(self, blobs: _BlobStore) -> None:
        twin = InMemoryTwinAPI.create()
        geometry = await _geometry(twin)
        field = _field_bytes()

        out = await _record(
            twin,
            field_blob=field,
            analysis={
                "geometry_node_id": str(geometry.id),
                "load_case_spec": {"load_force_n": [0, 0, -100]},
            },
        )

        assert out["field_stored"] is True
        wp = await twin.get_work_product(UUID(out["node_id"]))
        assert wp is not None
        md = wp.metadata
        # The summary JSON stays the primary blob.
        assert md["minio_object_key"].endswith(".json")
        assert md["field_object_key"].endswith(".field.json.gz")
        assert blobs.objects[md["field_object_key"]] == (field, "application/gzip")
        assert md["field_stored"] is True
        assert len(md["field_content_hash"]) == 64
        assert md["field_size_bytes"] == len(field)
        assert md["field_format"] == "metaforge.sim_field/1"
        assert md["field_quantities"] == ["displacement_magnitude", "von_mises"]
        assert md["field_analysis_type"] == "static_stress"
        assert md["field_ranges"]["von_mises"]["max"] == pytest.approx(100.0)
        # What was analysed, pinned.
        assert md["analysed_geometry"] == {
            "node_id": str(geometry.id),
            "revision": "B",
            "name": "Bracket",
            "content_hash": "geomhash123",
        }
        assert md["analysed_geometry_node_id"] == str(geometry.id)
        assert md["analysed_geometry_revision"] == "B"
        assert md["load_case_spec"] == {"load_force_n": [0, 0, -100]}
        # Fixtures and loads derived from the payload's own markers.
        assert md["fixtures"][0]["label"] == "Surface1"
        assert md["loads"][0]["vector"] == [0, 0, -100]
        # The analysed geometry becomes a DERIVES_FROM edge.
        edges = await twin.get_edges(wp.id, direction="outgoing")
        assert any(
            e.edge_type == EdgeType.DERIVES_FROM and e.target_id == geometry.id for e in edges
        )

    async def test_explicit_fixtures_and_revision_win(self, blobs: _BlobStore) -> None:
        twin = InMemoryTwinAPI.create()
        geometry = await _geometry(twin)
        out = await _record(
            twin,
            field_blob=_field_bytes(),
            analysis={
                "geometry_node_id": str(geometry.id),
                "geometry_revision": 7,
                "fixtures": [{"label": "bolt holes", "kind": "fixture"}],
            },
        )
        md = (await twin.get_work_product(UUID(out["node_id"]))).metadata  # type: ignore[union-attr]
        assert md["analysed_geometry"]["revision"] == 7
        assert md["fixtures"] == [{"label": "bolt holes", "kind": "fixture"}]

    async def test_not_a_field_payload_is_rejected_before_anything_is_created(
        self, blobs: _BlobStore
    ) -> None:
        twin = InMemoryTwinAPI.create()
        with pytest.raises(ValueError, match="gzipped JSON"):
            await _record(twin, field_blob=b"plain text")
        with pytest.raises(ValueError, match="metaforge.sim_field"):
            await _record(twin, field_blob=gzip.compress(b'{"format": "other"}'))
        listed = await twin.list_work_products(work_product_type=WorkProductType.SIMULATION_RESULT)
        assert listed == []
        assert blobs.objects == {}

    async def test_store_failure_still_records_the_summary(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = _BlobStore(fail_on=".field.json.gz")
        monkeypatch.setattr(
            "digital_twin.storage.work_product_blobs.store_work_product_blob", store.store
        )
        twin = InMemoryTwinAPI.create()
        out = await _record(twin, field_blob=_field_bytes())
        assert out["field_stored"] is False
        md = (await twin.get_work_product(UUID(out["node_id"]))).metadata  # type: ignore[union-attr]
        assert md["field_stored"] is False
        assert "field_object_key" not in md
        assert "minio down" in md["field_store_error"]
        assert md["max_von_mises_mpa"] == 100.0

    async def test_without_a_field_nothing_changes(self, blobs: _BlobStore) -> None:
        twin = InMemoryTwinAPI.create()
        out = await _record(twin)
        assert "field_stored" not in out
        md = (await twin.get_work_product(UUID(out["node_id"]))).metadata  # type: ignore[union-attr]
        assert not any(k.startswith("field_") for k in md)

    def test_decode_rejects_oversized_blobs(self) -> None:
        with pytest.raises(ValueError, match="cap"):
            decode_sim_field(b"\0" * (8 * 1024 * 1024 + 1))


class TestRecordDocumentTool:
    def _server(self, twin: InMemoryTwinAPI) -> TwinServer:
        return TwinServer(
            twin=twin, allow_mutations=True, document_recorder=make_document_recorder(twin, None)
        )

    async def test_field_file_is_read_by_reference(self, blobs: _BlobStore, tmp_path: Path) -> None:
        twin = InMemoryTwinAPI.create()
        geometry = await _geometry(twin)
        field_path = tmp_path / "bracket_solved_field.json.gz"
        field_path.write_bytes(_field_bytes())

        out = await self._server(twin).record_document(
            {
                "name": "Bracket FEA",
                "content": '{"max_von_mises_mpa": 100.0}',
                "document_type": "simulation_result",
                "field_file": str(field_path),
                "analysed_geometry_node_id": str(geometry.id),
            }
        )
        assert out["field_stored"] is True
        md = (await twin.get_work_product(UUID(out["node_id"]))).metadata  # type: ignore[union-attr]
        assert blobs.fetch(md["field_object_key"]) == field_path.read_bytes()
        assert md["analysed_geometry"]["content_hash"] == "geomhash123"

    async def test_relative_field_file_resolves_against_the_workspace(
        self, blobs: _BlobStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ADAPTER_WORKSPACE_DIR", str(tmp_path))
        (tmp_path / "f.json.gz").write_bytes(_field_bytes())
        twin = InMemoryTwinAPI.create()
        out = await self._server(twin).record_document(
            {
                "name": "R",
                "content": "{}",
                "document_type": "simulation_result",
                "field_file": "f.json.gz",
            }
        )
        assert out["field_stored"] is True

    async def test_field_base64_is_accepted(self, blobs: _BlobStore) -> None:
        twin = InMemoryTwinAPI.create()
        out = await self._server(twin).record_document(
            {
                "name": "R",
                "content": "{}",
                "document_type": "simulation_result",
                "field_base64": base64.b64encode(_field_bytes()).decode("ascii"),
            }
        )
        assert out["field_stored"] is True

    async def test_missing_field_file_is_a_clear_error(self, blobs: _BlobStore) -> None:
        with pytest.raises(ValueError, match="could not read field_file"):
            await self._server(InMemoryTwinAPI.create()).record_document(
                {
                    "name": "R",
                    "content": "{}",
                    "document_type": "simulation_result",
                    "field_file": "/nope/field.json.gz",
                }
            )

    async def test_field_args_only_apply_to_simulation_results(self, blobs: _BlobStore) -> None:
        with pytest.raises(ValueError, match="only apply to document_type='simulation_result'"):
            await self._server(InMemoryTwinAPI.create()).record_document(
                {"name": "R", "content": "x", "document_type": "prd", "field_file": "f"}
            )

    def test_schema_advertises_field_file(self) -> None:
        server = self._server(InMemoryTwinAPI.create())
        manifest = server._tools["twin.record_document"].manifest
        props = manifest.input_schema["properties"]
        assert {"field_file", "field_base64", "analysed_geometry_node_id", "fixtures"} <= set(props)


class TestFieldRoute:
    async def test_serves_the_stored_field_gzip_encoded(self, blobs: _BlobStore) -> None:
        twin = InMemoryTwinAPI.create()
        init_twin(twin)
        field = _field_bytes()
        out = await _record(twin, field_blob=field)

        app = FastAPI()
        app.include_router(router)
        response = TestClient(app).get(f"/v1/simulation/results/{out['node_id']}/field")
        assert response.status_code == 200
        assert response.headers["content-encoding"] == "gzip"
        assert response.headers["content-type"].startswith(
            "application/vnd.metaforge.sim-field+json"
        )
        assert response.headers["etag"] == f'"{out["field_content_hash"]}"'
        # The client inflates it transparently.
        body = response.json()
        assert body["format"] == "metaforge.sim_field"
        assert body["triangle_count"] == 20

    async def test_result_without_a_field_is_404_field_not_stored(self) -> None:
        twin = InMemoryTwinAPI.create()
        init_twin(twin)
        wp = await twin.create_work_product(
            WorkProduct(
                name="Old FEA",
                type=WorkProductType.SIMULATION_RESULT,
                domain="mechanical",
                file_path="",
                content_hash="a",
                format="json",
                created_by="test",
                metadata={"max_von_mises_mpa": 60.0},
            )
        )
        with pytest.raises(HTTPException) as exc:
            await get_simulation_result_field(str(wp.id))
        assert exc.value.status_code == 404
        assert exc.value.detail == "field not stored"

    async def test_unknown_and_malformed_ids(self) -> None:
        init_twin(InMemoryTwinAPI.create())
        with pytest.raises(HTTPException) as exc:
            await get_simulation_result_field("not-a-uuid")
        assert exc.value.status_code == 400
        with pytest.raises(HTTPException) as exc:
            await get_simulation_result_field("f8240b2a-9e01-4b16-83eb-b24cfcd4a04f")
        assert exc.value.status_code == 404

    async def test_storage_failure_is_502(
        self, blobs: _BlobStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        twin = InMemoryTwinAPI.create()
        init_twin(twin)
        out = await _record(twin, field_blob=_field_bytes())

        def _gone(_key: str) -> bytes:
            raise RuntimeError("object gone")

        monkeypatch.setattr("api_gateway.twin.blob_store.fetch_work_product_blob", _gone)
        with pytest.raises(HTTPException) as exc:
            await get_simulation_result_field(out["node_id"])
        assert exc.value.status_code == 502


class TestListingSurfacesTheField:
    async def test_has_field_and_analysis_context(self, blobs: _BlobStore) -> None:
        twin = InMemoryTwinAPI.create()
        geometry = await _geometry(twin)
        out = await _record(
            twin, field_blob=_field_bytes(), analysis={"geometry_node_id": str(geometry.id)}
        )
        wp = await twin.get_work_product(UUID(out["node_id"]))
        assert wp is not None
        r = _wp_to_simulation_result(wp)
        assert r.hasField is True
        assert r.analysisType == "static_stress"
        assert r.fieldQuantities == ["displacement_magnitude", "von_mises"]
        assert r.analysedGeometry is not None
        assert r.analysedGeometry["node_id"] == str(geometry.id)
        assert r.fixtures and r.fixtures[0]["label"] == "Surface1"

    def test_pre_field_result_lists_unchanged(self) -> None:
        wp = WorkProduct(
            name="Old",
            type=WorkProductType.SIMULATION_RESULT,
            domain="mechanical",
            file_path="",
            content_hash="a",
            format="json",
            created_by="t",
            metadata={
                "max_von_mises_mpa": 60.0,
                "mesh_convergence": {"converged": True, "points": []},
            },
        )
        r = _wp_to_simulation_result(wp)
        assert r.hasField is False
        assert r.fieldQuantities == []
        assert r.analysedGeometry is None
        assert r.maxVonMisesMpa == 60.0
        assert r.meshConvergence == {"converged": True, "points": []}
