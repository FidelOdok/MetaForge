"""Tests for work product version history (MET-251)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from api_gateway.twin.version_schemas import WorkProductRevision, WorkProductVersionHistory
from api_gateway.twin.version_service import VersionService
from twin_core.models.enums import WorkProductType
from twin_core.models.work_product import WorkProduct

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_wp(**kwargs) -> WorkProduct:
    defaults = dict(
        name="bracket",
        type=WorkProductType.CAD_MODEL,
        domain="mechanical",
        file_path="/tmp/bracket.step",
        content_hash="abc123",
        format="step",
        created_by="test",
        metadata={},
    )
    defaults.update(kwargs)
    return WorkProduct(**defaults)


# ---------------------------------------------------------------------------
# VersionService unit tests
# ---------------------------------------------------------------------------


class TestVersionServiceBuildRevision:
    def test_first_revision_numbered_one(self):
        wp = _make_wp()
        rev = VersionService.build_revision(wp, "Initial import")
        assert rev["revision"] == 1

    def test_second_revision_numbered_two(self):
        wp = _make_wp(metadata={"_revisions": [{"revision": 1}]})
        rev = VersionService.build_revision(wp, "Re-sync")
        assert rev["revision"] == 2

    def test_snapshot_excludes_internal_keys(self):
        wp = _make_wp(metadata={"volume": 1000, "_revisions": [{"revision": 1}]})
        rev = VersionService.build_revision(wp, "Re-sync")
        assert "_revisions" not in rev["metadata_snapshot"]
        assert rev["metadata_snapshot"]["volume"] == 1000

    def test_content_hash_captured(self):
        wp = _make_wp(content_hash="deadbeef")
        rev = VersionService.build_revision(wp, "Import")
        assert rev["content_hash"] == "deadbeef"

    def test_change_description_captured(self):
        wp = _make_wp()
        rev = VersionService.build_revision(wp, "Fixed fillets")
        assert rev["change_description"] == "Fixed fillets"


class TestVersionServiceAppendToMetadata:
    def test_appends_to_empty(self):
        meta = {"volume": 100}
        rev = {"revision": 1, "created_at": "2026-01-01T00:00:00+00:00"}
        result = VersionService.append_to_metadata(meta, rev)
        assert result["_revisions"] == [rev]
        assert result["volume"] == 100

    def test_appends_to_existing(self):
        existing_rev = {"revision": 1}
        meta = {"_revisions": [existing_rev]}
        rev2 = {"revision": 2}
        result = VersionService.append_to_metadata(meta, rev2)
        assert result["_revisions"] == [existing_rev, rev2]

    def test_does_not_mutate_input(self):
        meta = {"_revisions": [{"revision": 1}]}
        original_list = meta["_revisions"]
        VersionService.append_to_metadata(meta, {"revision": 2})
        assert meta["_revisions"] is original_list
        assert len(meta["_revisions"]) == 1


class TestVersionServiceGetHistory:
    def test_empty_history(self):
        wp = _make_wp()
        history = VersionService.get_history(wp)
        assert history.total == 0
        assert history.revisions == []

    def test_returns_revisions(self):
        wp = _make_wp(
            metadata={
                "_revisions": [
                    {
                        "revision": 1,
                        "created_at": "2026-01-01T00:00:00+00:00",
                        "content_hash": "abc",
                        "change_description": "Initial",
                        "metadata_snapshot": {},
                    },
                    {
                        "revision": 2,
                        "created_at": "2026-01-02T00:00:00+00:00",
                        "content_hash": "def",
                        "change_description": "Re-sync",
                        "metadata_snapshot": {"volume": 100},
                    },
                ]
            }
        )
        history = VersionService.get_history(wp)
        assert history.total == 2
        assert history.revisions[0].revision == 1
        assert history.revisions[1].revision == 2


class TestVersionServiceDiff:
    def _make_history(self, snapshots: list[dict]) -> WorkProductVersionHistory:
        revisions = [
            WorkProductRevision(
                revision=i + 1,
                created_at="2026-01-01T00:00:00+00:00",
                content_hash=f"hash{i}",
                change_description=f"Rev {i+1}",
                metadata_snapshot=snap,
            )
            for i, snap in enumerate(snapshots)
        ]
        return WorkProductVersionHistory(
            work_product_id="wp-1",
            revisions=revisions,
            total=len(revisions),
        )

    def test_changed_fields(self):
        history = self._make_history([{"volume": 100}, {"volume": 200}])
        diff = VersionService.diff(history, 1, 2)
        assert "volume" in diff.changed
        assert diff.changed["volume"].from_value == 100
        assert diff.changed["volume"].to_value == 200

    def test_added_fields(self):
        history = self._make_history([{}, {"part_count": 3}])
        diff = VersionService.diff(history, 1, 2)
        assert diff.added == {"part_count": 3}
        assert diff.changed == {}

    def test_removed_fields(self):
        history = self._make_history([{"old_key": "x"}, {}])
        diff = VersionService.diff(history, 1, 2)
        assert diff.removed == {"old_key": "x"}

    def test_no_changes(self):
        history = self._make_history([{"volume": 100}, {"volume": 100}])
        diff = VersionService.diff(history, 1, 2)
        assert diff.changed == {}
        assert diff.added == {}
        assert diff.removed == {}

    def test_out_of_range_raises(self):
        history = self._make_history([{}])
        with pytest.raises(ValueError, match="out of range"):
            VersionService.diff(history, 1, 5)

    def test_revision_ids_in_result(self):
        history = self._make_history([{}, {}])
        diff = VersionService.diff(history, 1, 2)
        assert diff.revision_a == 1
        assert diff.revision_b == 2
        assert diff.work_product_id == "wp-1"


# ---------------------------------------------------------------------------
# API endpoint tests (integration-style via ASGI test client)
# ---------------------------------------------------------------------------


class TestVersionEndpoints:
    @pytest.fixture(autouse=True)
    def _mock_storage(self):
        with patch("api_gateway.twin.routes.default_storage") as mock_st:
            mock_st.save.return_value = "/tmp/test/file.step"
            mock_st.content_hash.return_value = "abc123"
            yield mock_st

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

        # Clear twin state between tests
        _twin._graph._nodes.clear()
        _twin._graph._outgoing.clear()
        _twin._graph._incoming.clear()
        return _twin

    async def _import_step(self, client) -> str:
        """Helper: import a STEP file and return the work product ID."""
        with patch(
            "api_gateway.twin.import_service.ImportService.extract_metadata",
            new_callable=AsyncMock,
            return_value={"source": "basic", "file_size": 9},
        ):
            resp = await client.post(
                "/v1/twin/import",
                files={"file": ("bracket.step", b"STEP data", "application/octet-stream")},
            )
        assert resp.status_code == 201
        return resp.json()["id"]

    async def test_import_creates_initial_revision(self, client, twin):
        async with client:
            wp_id = await self._import_step(client)

        from uuid import UUID

        wp = await twin.get_work_product(UUID(wp_id))
        assert wp is not None
        revisions = wp.metadata.get("_revisions", [])
        assert len(revisions) == 1
        assert revisions[0]["change_description"] == "Initial import"

    async def test_get_versions_returns_history(self, client, twin):
        async with client:
            wp_id = await self._import_step(client)
            resp = await client.get(f"/v1/twin/nodes/{wp_id}/versions")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["revisions"][0]["revision"] == 1
        assert body["revisions"][0]["change_description"] == "Initial import"

    async def test_get_versions_unknown_node(self, client, twin):
        async with client:
            resp = await client.get(
                "/v1/twin/nodes/00000000-0000-0000-0000-000000000099/versions"
            )
        assert resp.status_code == 404

    async def test_iterate_adds_revision(self, client, twin):
        async with client:
            wp_id = await self._import_step(client)
            resp = await client.post(
                f"/v1/twin/nodes/{wp_id}/iterate",
                json={
                    "change_description": "Added mounting holes",
                    "metadata_updates": {"hole_count": 4},
                },
            )
        assert resp.status_code == 201
        body = resp.json()
        assert body["revision"] == 2
        assert body["change_description"] == "Added mounting holes"

    async def test_iterate_metadata_updated(self, client, twin):
        async with client:
            wp_id = await self._import_step(client)
            await client.post(
                f"/v1/twin/nodes/{wp_id}/iterate",
                json={
                    "change_description": "Updated",
                    "metadata_updates": {"custom_field": "hello"},
                },
            )

        from uuid import UUID

        wp = await twin.get_work_product(UUID(wp_id))
        assert wp is not None
        assert wp.metadata["custom_field"] == "hello"

    async def test_diff_between_revisions(self, client, twin):
        async with client:
            wp_id = await self._import_step(client)
            await client.post(
                f"/v1/twin/nodes/{wp_id}/iterate",
                json={
                    "change_description": "Volume change",
                    "metadata_updates": {"volume": 500},
                },
            )
            resp = await client.get(f"/v1/twin/nodes/{wp_id}/diff?v1=1&v2=2")
        assert resp.status_code == 200
        body = resp.json()
        assert body["revision_a"] == 1
        assert body["revision_b"] == 2
        # volume was added in revision 2
        assert "volume" in body["added"] or "volume" in body["changed"]

    async def test_diff_out_of_range(self, client, twin):
        async with client:
            wp_id = await self._import_step(client)
            resp = await client.get(f"/v1/twin/nodes/{wp_id}/diff?v1=1&v2=99")
        assert resp.status_code == 400

    async def test_diff_invalid_node(self, client, twin):
        async with client:
            resp = await client.get(
                "/v1/twin/nodes/00000000-0000-0000-0000-000000000099/diff?v1=1&v2=2"
            )
        assert resp.status_code == 404
