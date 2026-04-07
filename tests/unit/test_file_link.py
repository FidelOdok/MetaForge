"""Tests for file linking API (MET-252, MET-285)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from api_gateway.twin.file_link import (
    FileLink,
    FileLinkStore,
    check_sync_status,
    sync_linked_file,
)

# ---------------------------------------------------------------------------
# FileLinkStore
# ---------------------------------------------------------------------------


class TestFileLinkStore:
    def test_create_and_get(self):
        store = FileLinkStore()
        link = FileLink(
            work_product_id="wp-1",
            source_path="/tmp/test.step",
            source_hash="abc123",
        )
        store.create(link)
        assert store.get("wp-1") is link

    def test_get_missing(self):
        store = FileLinkStore()
        assert store.get("missing") is None

    def test_list_all(self):
        store = FileLinkStore()
        store.create(FileLink(work_product_id="wp-1", source_path="/a.step"))
        store.create(FileLink(work_product_id="wp-2", source_path="/b.step"))
        assert len(store.list_all()) == 2

    def test_delete(self):
        store = FileLinkStore()
        store.create(FileLink(work_product_id="wp-1", source_path="/a.step"))
        assert store.delete("wp-1") is True
        assert store.get("wp-1") is None

    def test_delete_missing(self):
        store = FileLinkStore()
        assert store.delete("missing") is False

    def test_update(self):
        store = FileLinkStore()
        store.create(
            FileLink(
                work_product_id="wp-1",
                source_path="/a.step",
                sync_status="synced",
            )
        )
        store.update("wp-1", sync_status="changed")
        assert store.get("wp-1").sync_status == "changed"

    def test_update_missing(self):
        store = FileLinkStore()
        assert store.update("missing", sync_status="changed") is None


# ---------------------------------------------------------------------------
# check_sync_status
# ---------------------------------------------------------------------------


class TestCheckSyncStatus:
    def test_disconnected_when_file_missing(self):
        link = FileLink(
            work_product_id="wp-1",
            source_path="/nonexistent/file.step",
            source_hash="abc",
        )
        assert check_sync_status(link) == "disconnected"

    def test_synced_when_hash_matches(self, tmp_path):
        f = tmp_path / "test.step"
        f.write_bytes(b"hello")
        from api_gateway.twin.file_link import _file_hash

        h = _file_hash(str(f))
        link = FileLink(
            work_product_id="wp-1",
            source_path=str(f),
            source_hash=h,
        )
        assert check_sync_status(link) == "synced"

    def test_changed_when_hash_differs(self, tmp_path):
        f = tmp_path / "test.step"
        f.write_bytes(b"hello")
        link = FileLink(
            work_product_id="wp-1",
            source_path=str(f),
            source_hash="old_hash",
        )
        assert check_sync_status(link) == "changed"


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------


class TestFileLinkEndpoints:
    @pytest.fixture(autouse=True)
    def _reset_store(self):
        """Reset the link store between tests."""
        from api_gateway.twin.file_link import link_store

        link_store._links.clear()
        yield
        link_store._links.clear()

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
    def temp_file(self, tmp_path):
        f = tmp_path / "drone.kicad_sch"
        f.write_text('(kicad_sch (version 20230121) (symbol (lib_id "R")))')
        return str(f)

    async def test_create_link(self, client, temp_file):
        # First create a work product to link to
        from api_gateway.twin.routes import _twin
        from twin_core.models.enums import WorkProductType
        from twin_core.models.work_product import WorkProduct

        wp = WorkProduct(
            name="test-sch",
            type=WorkProductType.SCHEMATIC,
            domain="electronics",
            file_path="/stored/test.kicad_sch",
            content_hash="abc",
            format="kicad_sch",
            created_by="test",
        )
        created = await _twin.create_work_product(wp)

        async with client:
            resp = await client.post(
                f"/v1/twin/nodes/{created.id}/link",
                json={
                    "source_path": temp_file,
                    "tool": "kicad",
                    "watch": True,
                },
            )
        assert resp.status_code == 201
        body = resp.json()
        assert body["work_product_id"] == str(created.id)
        assert body["source_path"] == temp_file
        assert body["tool"] == "kicad"
        assert body["sync_status"] == "synced"
        assert body["source_hash"]  # non-empty

    async def test_create_link_file_not_found(self, client):
        from api_gateway.twin.routes import _twin
        from twin_core.models.enums import WorkProductType
        from twin_core.models.work_product import WorkProduct

        wp = WorkProduct(
            name="test",
            type=WorkProductType.CAD_MODEL,
            domain="mechanical",
            file_path="/x",
            content_hash="abc",
            format="step",
            created_by="test",
        )
        created = await _twin.create_work_product(wp)

        async with client:
            resp = await client.post(
                f"/v1/twin/nodes/{created.id}/link",
                json={"source_path": "/nonexistent/file.step"},
            )
        assert resp.status_code == 400
        assert "not found" in resp.json()["detail"]

    async def test_get_link(self, client, temp_file):
        from api_gateway.twin.file_link import FileLink, _file_hash, link_store

        h = _file_hash(temp_file)
        link_store.create(
            FileLink(
                work_product_id="00000000-0000-0000-0000-000000000001",
                source_path=temp_file,
                source_hash=h,
                sync_status="synced",
            )
        )

        async with client:
            resp = await client.get("/v1/twin/nodes/00000000-0000-0000-0000-000000000001/link")
        assert resp.status_code == 200
        assert resp.json()["sync_status"] == "synced"

    async def test_get_link_detects_change(self, client, temp_file):
        from api_gateway.twin.file_link import FileLink, link_store

        link_store.create(
            FileLink(
                work_product_id="00000000-0000-0000-0000-000000000002",
                source_path=temp_file,
                source_hash="stale_hash",
                sync_status="synced",
            )
        )

        async with client:
            resp = await client.get("/v1/twin/nodes/00000000-0000-0000-0000-000000000002/link")
        assert resp.status_code == 200
        assert resp.json()["sync_status"] == "changed"

    async def test_delete_link(self, client):
        from api_gateway.twin.file_link import FileLink, link_store

        link_store.create(
            FileLink(
                work_product_id="00000000-0000-0000-0000-000000000003",
                source_path="/tmp/x.step",
            )
        )

        async with client:
            resp = await client.delete("/v1/twin/nodes/00000000-0000-0000-0000-000000000003/link")
        assert resp.status_code == 204

    async def test_delete_link_not_found(self, client):
        async with client:
            resp = await client.delete("/v1/twin/nodes/00000000-0000-0000-0000-000000000099/link")
        assert resp.status_code == 404

    async def test_list_links(self, client, temp_file):
        from api_gateway.twin.file_link import FileLink, _file_hash, link_store

        h = _file_hash(temp_file)
        link_store.create(
            FileLink(
                work_product_id="wp-a",
                source_path=temp_file,
                source_hash=h,
            )
        )

        async with client:
            resp = await client.get("/v1/twin/links")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["work_product_id"] == "wp-a"

    async def test_sync_no_changes(self, client, temp_file):
        from api_gateway.twin.file_link import FileLink, _file_hash, link_store

        h = _file_hash(temp_file)
        link_store.create(
            FileLink(
                work_product_id="00000000-0000-0000-0000-000000000004",
                source_path=temp_file,
                source_hash=h,
            )
        )

        async with client:
            resp = await client.post("/v1/twin/nodes/00000000-0000-0000-0000-000000000004/sync")
        assert resp.status_code == 200
        assert resp.json()["status"] == "synced"
        assert resp.json()["message"] == "No changes detected"


# ---------------------------------------------------------------------------
# sync_linked_file — metadata merge (MET-285)
# ---------------------------------------------------------------------------


class TestSyncMetadataMerge:
    """Verify that sync_linked_file merges metadata instead of replacing it."""

    @pytest.fixture
    def temp_step(self, tmp_path):
        """A dummy STEP file with a hash that differs from the link's stored hash."""
        f = tmp_path / "part.step"
        f.write_bytes(b"ISO-10303-21; /* fake step content */")
        return f

    def _make_link(self, path: str, stored_hash: str = "old_hash") -> FileLink:
        return FileLink(
            work_product_id="00000000-0000-0000-0000-000000000010",
            source_path=path,
            tool="freecad",
            source_hash=stored_hash,
        )

    async def test_existing_metadata_preserved_after_sync(self, temp_step):
        """Geometry fields present on the node before sync survive unchanged."""
        from twin_core.api import InMemoryTwinAPI
        from twin_core.models.enums import WorkProductType
        from twin_core.models.work_product import WorkProduct

        twin = InMemoryTwinAPI.create()
        wp = WorkProduct(
            name="part",
            type=WorkProductType.CAD_MODEL,
            domain="mechanical",
            file_path=str(temp_step),
            content_hash="old_hash",
            format="step",
            created_by="test",
            metadata={"volume": 1000, "surface_area": 500},
        )
        import uuid

        wp.id = uuid.UUID("00000000-0000-0000-0000-000000000010")
        await twin.create_work_product(wp)

        link = self._make_link(str(temp_step))

        with patch(
            "api_gateway.twin.import_service.ImportService.extract_metadata",
            new_callable=AsyncMock,
            return_value={},
        ):
            result = await sync_linked_file(link, twin)

        assert result["status"] == "synced"
        updated = await twin.get_work_product(uuid.UUID("00000000-0000-0000-0000-000000000010"))
        assert updated is not None
        assert updated.metadata["volume"] == 1000
        assert updated.metadata["surface_area"] == 500

    async def test_sync_fields_always_written(self, temp_step):
        """synced_from, sync_tool, last_synced_at, source_hash are always present."""
        from twin_core.api import InMemoryTwinAPI
        from twin_core.models.enums import WorkProductType
        from twin_core.models.work_product import WorkProduct

        twin = InMemoryTwinAPI.create()
        import uuid

        wp = WorkProduct(
            name="part",
            type=WorkProductType.CAD_MODEL,
            domain="mechanical",
            file_path=str(temp_step),
            content_hash="old_hash",
            format="step",
            created_by="test",
        )
        wp.id = uuid.UUID("00000000-0000-0000-0000-000000000010")
        await twin.create_work_product(wp)

        link = self._make_link(str(temp_step))

        with patch(
            "api_gateway.twin.import_service.ImportService.extract_metadata",
            new_callable=AsyncMock,
            return_value={},
        ):
            result = await sync_linked_file(link, twin)

        assert result["status"] == "synced"
        updated = await twin.get_work_product(uuid.UUID("00000000-0000-0000-0000-000000000010"))
        assert updated is not None
        meta = updated.metadata
        assert meta["synced_from"] == str(temp_step)
        assert meta["sync_tool"] == "freecad"
        assert "last_synced_at" in meta
        assert "source_hash" in meta

    async def test_conflicting_key_overwritten_by_sync(self, temp_step):
        """A key present in both existing metadata and extracted metadata uses the sync value."""
        from twin_core.api import InMemoryTwinAPI
        from twin_core.models.enums import WorkProductType
        from twin_core.models.work_product import WorkProduct

        twin = InMemoryTwinAPI.create()
        import uuid

        wp = WorkProduct(
            name="part",
            type=WorkProductType.CAD_MODEL,
            domain="mechanical",
            file_path=str(temp_step),
            content_hash="old_hash",
            format="step",
            created_by="test",
            metadata={"format": "step"},
        )
        wp.id = uuid.UUID("00000000-0000-0000-0000-000000000010")
        await twin.create_work_product(wp)

        link = self._make_link(str(temp_step))

        with patch(
            "api_gateway.twin.import_service.ImportService.extract_metadata",
            new_callable=AsyncMock,
            return_value={"format": "kicad_sch"},
        ):
            result = await sync_linked_file(link, twin)

        assert result["status"] == "synced"
        updated = await twin.get_work_product(uuid.UUID("00000000-0000-0000-0000-000000000010"))
        assert updated is not None
        assert updated.metadata["format"] == "kicad_sch"

    async def test_no_existing_wp_graceful(self, temp_step):
        """If get_work_product returns None, sync still succeeds with just sync metadata."""
        mock_twin = AsyncMock()
        mock_twin.get_work_product.return_value = None
        mock_twin.update_work_product.return_value = AsyncMock()

        link = self._make_link(str(temp_step))

        with patch(
            "api_gateway.twin.import_service.ImportService.extract_metadata",
            new_callable=AsyncMock,
            return_value={"component_count": 5},
        ):
            result = await sync_linked_file(link, mock_twin)

        assert result["status"] == "synced"
        call_args = mock_twin.update_work_product.call_args
        updates_passed = call_args[0][1]
        meta = updates_passed["metadata"]
        assert meta["synced_from"] == str(temp_step)
        assert meta["component_count"] == 5
