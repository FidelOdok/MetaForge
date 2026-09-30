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
                change_description=f"Rev {i + 1}",
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
            resp = await client.get("/v1/twin/nodes/00000000-0000-0000-0000-000000000099/versions")
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

    async def test_iterate_saves_a_robot_pose_readable_from_get_node(self, client, twin):
        """FORGE-250: "Save current pose" reuses this generic /iterate
        endpoint -- metadata_updates={"poses": {...}} merges into the
        node's metadata and the new `poses` field on GET /nodes/{id}
        (TwinNodeResponse.poses) surfaces it back to the dashboard."""
        async with client:
            wp_id = await self._import_step(client)

            resp = await client.get(f"/v1/twin/nodes/{wp_id}")
            assert resp.json()["poses"] is None

            await client.post(
                f"/v1/twin/nodes/{wp_id}/iterate",
                json={
                    "change_description": 'Saved pose "Home"',
                    "metadata_updates": {"poses": {"Home": {"joint_1": 0.0, "joint_2": 2.0}}},
                },
            )
            resp = await client.get(f"/v1/twin/nodes/{wp_id}")

        assert resp.json()["poses"] == {"Home": {"joint_1": 0.0, "joint_2": 2.0}}

    async def test_iterate_saving_a_second_pose_does_not_drop_the_first(self, client, twin):
        async with client:
            wp_id = await self._import_step(client)
            await client.post(
                f"/v1/twin/nodes/{wp_id}/iterate",
                json={
                    "change_description": 'Saved pose "Home"',
                    "metadata_updates": {"poses": {"Home": {"joint_1": 0.0}}},
                },
            )
            await client.post(
                f"/v1/twin/nodes/{wp_id}/iterate",
                json={
                    "change_description": 'Saved pose "Extended"',
                    "metadata_updates": {
                        "poses": {"Home": {"joint_1": 0.0}, "Extended": {"joint_1": 1.0}}
                    },
                },
            )
            resp = await client.get(f"/v1/twin/nodes/{wp_id}")

        assert resp.json()["poses"] == {"Home": {"joint_1": 0.0}, "Extended": {"joint_1": 1.0}}

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


class TestGeometryDiffEndpoint:
    """GET /v1/twin/nodes/{id}/geometry-diff (FORGE-301) -- the route layer
    over api_gateway.twin.geometry_diff; the evaluator itself is unit-tested
    directly in tests/unit/test_geometry_diff.py."""

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

    @pytest.fixture(autouse=True)
    def _reset_geometry_diff(self):
        from api_gateway.twin.routes import init_geometry_diff

        yield
        init_geometry_diff(None)  # never leak a test double into another test module

    async def test_not_configured_returns_503(self, client):
        from api_gateway.twin.routes import init_geometry_diff

        init_geometry_diff(None)
        async with client:
            resp = await client.get(
                "/v1/twin/nodes/00000000-0000-0000-0000-000000000099/geometry-diff"
            )
        assert resp.status_code == 503

    async def test_lookup_error_returns_404(self, client):
        from api_gateway.twin.routes import init_geometry_diff

        async def fake_diff(*, work_product_id: str) -> dict:
            raise LookupError("work product has no prior version (no SUPERSEDES edge)")

        init_geometry_diff(fake_diff)
        async with client:
            resp = await client.get(
                "/v1/twin/nodes/00000000-0000-0000-0000-000000000099/geometry-diff"
            )
        assert resp.status_code == 404

    async def test_value_error_returns_400(self, client):
        from api_gateway.twin.routes import init_geometry_diff

        async def fake_diff(*, work_product_id: str) -> dict:
            raise ValueError("current work product has format 'stl', expected step")

        init_geometry_diff(fake_diff)
        async with client:
            resp = await client.get(
                "/v1/twin/nodes/00000000-0000-0000-0000-000000000099/geometry-diff"
            )
        assert resp.status_code == 400

    async def test_unexpected_error_returns_502(self, client):
        from api_gateway.twin.routes import init_geometry_diff

        async def fake_diff(*, work_product_id: str) -> dict:
            raise RuntimeError("adapter unreachable")

        init_geometry_diff(fake_diff)
        async with client:
            resp = await client.get(
                "/v1/twin/nodes/00000000-0000-0000-0000-000000000099/geometry-diff"
            )
        assert resp.status_code == 502

    async def test_happy_path_returns_real_shape(self, client):
        from api_gateway.twin.routes import init_geometry_diff

        async def fake_diff(*, work_product_id: str) -> dict:
            return {
                "current_work_product_id": work_product_id,
                "previous_work_product_id": "11111111-1111-1111-1111-111111111111",
                "current_volume_mm3": 1500.0,
                "previous_volume_mm3": 1000.0,
                "volume_delta_mm3": 500.0,
                "current_area_mm2": 900.0,
                "previous_area_mm2": 700.0,
                "area_delta_mm2": 200.0,
                "current_bounding_box": {"x_min": 0, "x_max": 10},
                "previous_bounding_box": {"x_min": 0, "x_max": 8},
            }

        init_geometry_diff(fake_diff)
        node_id = "00000000-0000-0000-0000-000000000099"
        async with client:
            resp = await client.get(f"/v1/twin/nodes/{node_id}/geometry-diff")
        assert resp.status_code == 200
        body = resp.json()
        assert body["current_work_product_id"] == node_id
        assert body["volume_delta_mm3"] == 500.0
        assert body["current_bounding_box"] == {"x_min": 0, "x_max": 10}
