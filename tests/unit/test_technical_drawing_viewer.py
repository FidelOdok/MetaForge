"""Unit tests for technical_drawing_viewer.py's list/approve closures and
their REST routes (FORGE-293, gap G-H1).

The recorder half (``make_technical_drawing_recorder``) already existed and
is tested in ``test_structured_document_recorder.py``/
``test_generate_technical_drawing.py``; these tests exercise the two things
this ticket actually adds: listing a part's real recorded drawings by
walking the real ``PARENT_OF`` edge, and the approval state transition.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from api_gateway.twin.structured_document_recorder import make_technical_drawing_recorder
from api_gateway.twin.technical_drawing_viewer import (
    make_technical_drawing_approver,
    make_technical_drawing_lister,
)

_DIMENSIONS = [{"feature": "bore_diameter", "nominal_mm": 10.0, "tolerance_plus_mm": 0.02}]
_GDT = [{"feature": "mounting_face", "symbol": "flatness", "tolerance_value_mm": 0.05}]
_FINISHES = [{"feature": "bore_surface", "ra_um": 1.6}]
_INSPECTION = ["CMM bore diameter check"]


@pytest.fixture()
def patched_blob_store(monkeypatch: pytest.MonkeyPatch) -> dict:
    captured: dict = {"calls": []}

    def fake_store(node_id: str, filename: str, content: bytes, content_type: str = "") -> str:
        key = f"work-products/{node_id}/{filename}"
        captured["calls"].append({"node_id": node_id, "filename": filename, "key": key})
        return key

    import digital_twin.storage.work_product_blobs as blobs

    monkeypatch.setattr(blobs, "store_work_product_blob", fake_store)
    return captured


async def _commit_part(twin) -> str:
    """A minimal real CAD_MODEL work product to hang a drawing off of."""
    from datetime import UTC, datetime

    from twin_core.models.enums import WorkProductType
    from twin_core.models.work_product import WorkProduct

    wp = WorkProduct(
        id=uuid4(),
        name="Elbow Bracket",
        type=WorkProductType.CAD_MODEL,
        domain="mechanical",
        file_path="",
        content_hash="deadbeef",
        format="step",
        metadata={},
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        created_by="test",
    )
    created = await twin.create_work_product(wp)
    return str(created.id)


class TestTechnicalDrawingRecorderApprovalDefault:
    async def test_commit_initializes_unapproved(self, patched_blob_store: dict) -> None:
        from twin_core.api import InMemoryTwinAPI

        twin = InMemoryTwinAPI.create()
        part_id = await _commit_part(twin)
        commit = make_technical_drawing_recorder(twin, None)

        result = await commit(
            name="Elbow Bracket Drawing",
            part_name="Elbow Bracket",
            dimensions=_DIMENSIONS,
            gdt_callouts=_GDT,
            surface_finishes=_FINISHES,
            inspection_requirements=_INSPECTION,
            source_node_ids=[part_id],
        )
        wp = await twin.get_work_product(UUID(result["node_id"]))
        assert wp.metadata["approved"] is False
        assert wp.metadata["approved_at"] is None


class TestTechnicalDrawingLister:
    async def test_lists_real_drawing_linked_to_its_part(self, patched_blob_store: dict) -> None:
        from twin_core.api import InMemoryTwinAPI

        twin = InMemoryTwinAPI.create()
        part_id = await _commit_part(twin)
        commit = make_technical_drawing_recorder(twin, None)
        lister = make_technical_drawing_lister(twin)

        created = await commit(
            name="Elbow Bracket Drawing",
            part_name="Elbow Bracket",
            dimensions=_DIMENSIONS,
            gdt_callouts=_GDT,
            surface_finishes=_FINISHES,
            inspection_requirements=_INSPECTION,
            source_node_ids=[part_id],
        )

        drawings = await lister(work_product_id=part_id)

        assert len(drawings) == 1
        d = drawings[0]
        assert d["node_id"] == created["node_id"]
        assert d["part_name"] == "Elbow Bracket"
        assert d["dimensions"] == _DIMENSIONS
        assert d["gdt_callouts"] == _GDT
        assert d["surface_finishes"] == _FINISHES
        assert d["inspection_requirements"] == _INSPECTION
        assert d["approved"] is False

    async def test_no_drawings_returns_empty_list(self, patched_blob_store: dict) -> None:
        from twin_core.api import InMemoryTwinAPI

        twin = InMemoryTwinAPI.create()
        part_id = await _commit_part(twin)
        lister = make_technical_drawing_lister(twin)

        assert await lister(work_product_id=part_id) == []

    async def test_ignores_non_technical_drawing_parent_of_edges(
        self, patched_blob_store: dict
    ) -> None:
        """A different work product linked via the same PARENT_OF edge
        shape (e.g. a CAD_SOURCE_SCRIPT -> CAD_MODEL provenance edge) must
        not be surfaced as a drawing."""
        from datetime import UTC, datetime

        from twin_core.api import InMemoryTwinAPI
        from twin_core.models.enums import EdgeType, WorkProductType
        from twin_core.models.work_product import WorkProduct

        twin = InMemoryTwinAPI.create()
        part_id = await _commit_part(twin)

        other = WorkProduct(
            id=uuid4(),
            name="Not a drawing",
            type=WorkProductType.CAD_SOURCE_SCRIPT,
            domain="mechanical",
            file_path="",
            content_hash="deadbeef",
            format="py",
            metadata={},
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            created_by="test",
        )
        created_other = await twin.create_work_product(other)
        await twin.add_edge(created_other.id, UUID(part_id), EdgeType.PARENT_OF)

        lister = make_technical_drawing_lister(twin)
        assert await lister(work_product_id=part_id) == []

    async def test_multiple_drawings_oldest_first(self, patched_blob_store: dict) -> None:
        from twin_core.api import InMemoryTwinAPI

        twin = InMemoryTwinAPI.create()
        part_id = await _commit_part(twin)
        commit = make_technical_drawing_recorder(twin, None)
        lister = make_technical_drawing_lister(twin)

        first = await commit(
            name="Rev A",
            part_name="Elbow Bracket",
            dimensions=_DIMENSIONS,
            source_node_ids=[part_id],
        )
        second = await commit(
            name="Rev B",
            part_name="Elbow Bracket",
            dimensions=_DIMENSIONS,
            source_node_ids=[part_id],
        )

        drawings = await lister(work_product_id=part_id)
        assert [d["node_id"] for d in drawings] == [first["node_id"], second["node_id"]]


class TestTechnicalDrawingApprove:
    async def test_approve_flips_gate_and_records_version(self, patched_blob_store: dict) -> None:
        from twin_core.api import InMemoryTwinAPI

        twin = InMemoryTwinAPI.create()
        part_id = await _commit_part(twin)
        commit = make_technical_drawing_recorder(twin, None)
        approve = make_technical_drawing_approver(twin)

        created = await commit(
            name="Elbow Bracket Drawing",
            part_name="Elbow Bracket",
            dimensions=_DIMENSIONS,
            source_node_ids=[part_id],
        )
        result = await approve(created["node_id"], approved_by="fidel")

        assert result["approved"] is True
        assert result["approved_at"]

        wp = await twin.get_work_product(UUID(created["node_id"]))
        assert wp.metadata["approved"] is True
        assert wp.metadata["approved_by"] == "fidel"
        revisions = wp.metadata["_revisions"]
        assert len(revisions) == 1
        assert revisions[0]["metadata_snapshot"]["approved"] is True

    async def test_approve_missing_node_raises(self) -> None:
        from twin_core.api import InMemoryTwinAPI

        twin = InMemoryTwinAPI.create()
        approve = make_technical_drawing_approver(twin)
        with pytest.raises(ValueError, match="not found"):
            await approve("11111111-1111-1111-1111-111111111111")

    async def test_double_approve_raises(self, patched_blob_store: dict) -> None:
        from twin_core.api import InMemoryTwinAPI

        twin = InMemoryTwinAPI.create()
        part_id = await _commit_part(twin)
        commit = make_technical_drawing_recorder(twin, None)
        approve = make_technical_drawing_approver(twin)
        created = await commit(
            name="Elbow Bracket Drawing",
            part_name="Elbow Bracket",
            dimensions=_DIMENSIONS,
            source_node_ids=[part_id],
        )

        await approve(created["node_id"])
        with pytest.raises(ValueError, match="already approved"):
            await approve(created["node_id"])

    async def test_approve_wrong_wp_type_raises(self, patched_blob_store: dict) -> None:
        from twin_core.api import InMemoryTwinAPI

        twin = InMemoryTwinAPI.create()
        part_id = await _commit_part(twin)
        approve = make_technical_drawing_approver(twin)
        with pytest.raises(ValueError, match="not a technical_drawing"):
            await approve(part_id)

    async def test_missing_approved_key_treated_as_unapproved(
        self, patched_blob_store: dict
    ) -> None:
        """A drawing recorded before this ticket (no 'approved' key at all)
        must still approve cleanly -- no backfill migration needed."""
        from datetime import UTC, datetime

        from twin_core.api import InMemoryTwinAPI
        from twin_core.models.enums import WorkProductType
        from twin_core.models.work_product import WorkProduct

        twin = InMemoryTwinAPI.create()
        wp = WorkProduct(
            id=uuid4(),
            name="Pre-existing Drawing",
            type=WorkProductType.TECHNICAL_DRAWING,
            domain="mechanical",
            file_path="",
            content_hash="deadbeef",
            format="md",
            metadata={"part_name": "Legacy Part", "dimensions": _DIMENSIONS},
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            created_by="test",
        )
        created = await twin.create_work_product(wp)
        approve = make_technical_drawing_approver(twin)

        result = await approve(str(created.id))
        assert result["approved"] is True


class TestTechnicalDrawingListRoute:
    """GET /v1/technical-drawings?work_product_id=..."""

    @staticmethod
    def _patch_blobs(monkeypatch: pytest.MonkeyPatch) -> None:
        import digital_twin.storage.work_product_blobs as blobs

        monkeypatch.setattr(
            blobs,
            "store_work_product_blob",
            lambda nid, fn, content, content_type="": f"work-products/{nid}/{fn}",
        )

    @pytest.fixture(autouse=True)
    def _wire_lister(self, monkeypatch: pytest.MonkeyPatch):
        import api_gateway.technical_drawings.routes as routes
        import api_gateway.twin.routes as twin_routes

        self._patch_blobs(monkeypatch)
        lister = make_technical_drawing_lister(twin_routes._twin)
        routes.init_technical_drawing_lister(lister)
        yield
        routes.init_technical_drawing_lister(None)

    @pytest.fixture
    def app(self):
        from fastapi import FastAPI

        from api_gateway.technical_drawings.routes import router

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

    async def test_list_returns_real_drawing(self, client, twin) -> None:
        part_id = await _commit_part(twin)
        commit = make_technical_drawing_recorder(twin, None)
        created = await commit(
            name="Elbow Bracket Drawing",
            part_name="Elbow Bracket",
            dimensions=_DIMENSIONS,
            source_node_ids=[part_id],
        )
        async with client:
            resp = await client.get("/v1/technical-drawings", params={"work_product_id": part_id})
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["drawings"]) == 1
        assert body["drawings"][0]["node_id"] == created["node_id"]
        assert body["drawings"][0]["part_name"] == "Elbow Bracket"

    async def test_list_not_configured_503(self, client) -> None:
        import api_gateway.technical_drawings.routes as routes

        routes.init_technical_drawing_lister(None)
        async with client:
            resp = await client.get(
                "/v1/technical-drawings", params={"work_product_id": str(uuid4())}
            )
        assert resp.status_code == 503


class TestApproveTechnicalDrawingRoute:
    """POST /v1/twin/nodes/{id}/approve-technical-drawing"""

    @staticmethod
    def _patch_blobs(monkeypatch: pytest.MonkeyPatch) -> None:
        import digital_twin.storage.work_product_blobs as blobs

        monkeypatch.setattr(
            blobs,
            "store_work_product_blob",
            lambda nid, fn, content, content_type="": f"work-products/{nid}/{fn}",
        )

    @pytest.fixture(autouse=True)
    def _wire_approver(self, monkeypatch: pytest.MonkeyPatch):
        import api_gateway.twin.routes as routes

        self._patch_blobs(monkeypatch)
        approver = make_technical_drawing_approver(routes._twin)
        routes.init_technical_drawing_approver(approver)
        yield
        routes.init_technical_drawing_approver(None)

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

    async def _create_drawing(self, twin) -> str:
        part_id = await _commit_part(twin)
        commit = make_technical_drawing_recorder(twin, None)
        result = await commit(
            name="Elbow Bracket Drawing",
            part_name="Elbow Bracket",
            dimensions=_DIMENSIONS,
            source_node_ids=[part_id],
        )
        return result["node_id"]

    async def test_approve_success(self, client, twin) -> None:
        node_id = await self._create_drawing(twin)
        async with client:
            resp = await client.post(f"/v1/twin/nodes/{node_id}/approve-technical-drawing", json={})
        assert resp.status_code == 200
        body = resp.json()
        assert body["approved"] is True
        assert body["node_id"] == node_id

    async def test_approve_unknown_node_404(self, client, twin) -> None:
        async with client:
            resp = await client.post(
                "/v1/twin/nodes/00000000-0000-0000-0000-000000000099/approve-technical-drawing",
                json={},
            )
        assert resp.status_code == 404

    async def test_approve_wrong_wp_type_400(self, client, twin) -> None:
        part_id = await _commit_part(twin)
        async with client:
            resp = await client.post(f"/v1/twin/nodes/{part_id}/approve-technical-drawing", json={})
        assert resp.status_code == 400

    async def test_double_approve_409(self, client, twin) -> None:
        node_id = await self._create_drawing(twin)
        async with client:
            resp1 = await client.post(
                f"/v1/twin/nodes/{node_id}/approve-technical-drawing", json={}
            )
            assert resp1.status_code == 200
            resp2 = await client.post(
                f"/v1/twin/nodes/{node_id}/approve-technical-drawing", json={}
            )
        assert resp2.status_code == 409

    async def test_approver_not_configured_503(self, client, twin) -> None:
        import api_gateway.twin.routes as routes

        node_id = await self._create_drawing(twin)
        routes.init_technical_drawing_approver(None)
        async with client:
            resp = await client.post(f"/v1/twin/nodes/{node_id}/approve-technical-drawing", json={})
        assert resp.status_code == 503


class TestWpToResponseTechnicalDrawingField:
    """GET /v1/twin/nodes surfaces a technical_drawing node's structured
    data via the technicalDrawing field (mirrors meshStats/poses)."""

    async def test_technical_drawing_field_populated(self, patched_blob_store: dict) -> None:
        from api_gateway.twin.routes import _wp_to_response
        from twin_core.api import InMemoryTwinAPI

        twin_api = InMemoryTwinAPI.create()
        part_id = await _commit_part(twin_api)
        commit = make_technical_drawing_recorder(twin_api, None)
        created = await commit(
            name="Elbow Bracket Drawing",
            part_name="Elbow Bracket",
            dimensions=_DIMENSIONS,
            source_node_ids=[part_id],
        )
        wp = await twin_api.get_work_product(UUID(created["node_id"]))
        response = _wp_to_response(wp)
        assert response.technicalDrawing is not None
        assert response.technicalDrawing["part_name"] == "Elbow Bracket"
        assert response.technicalDrawing["dimensions"] == _DIMENSIONS
        assert response.technicalDrawing["approved"] is False

    async def test_technical_drawing_field_none_for_other_types(self) -> None:
        from datetime import UTC, datetime

        from api_gateway.twin.routes import _wp_to_response
        from twin_core.models.enums import WorkProductType
        from twin_core.models.work_product import WorkProduct

        wp = WorkProduct(
            id=uuid4(),
            name="Not a drawing",
            type=WorkProductType.CAD_MODEL,
            domain="mechanical",
            file_path="",
            content_hash="deadbeef",
            format="step",
            metadata={},
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            created_by="test",
        )
        response = _wp_to_response(wp)
        assert response.technicalDrawing is None
