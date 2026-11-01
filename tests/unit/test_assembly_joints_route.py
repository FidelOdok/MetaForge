"""Tests for PATCH /v1/twin/nodes/{node_id}/assembly-joints (FORGE-271)."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from twin_core.models.enums import WorkProductType
from twin_core.models.work_product import WorkProduct


class TestUpdateAssemblyJoints:
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

    async def _make_node(self, twin, *, assembly: dict | None = None) -> str:
        wp = WorkProduct(
            name="Arm Assembly",
            type=WorkProductType.CAD_MODEL,
            domain="mechanical",
            file_path="",
            content_hash="deadbeef",
            format="step",
            created_by="test",
            metadata={"assembly": assembly} if assembly is not None else {},
        )
        created = await twin.create_work_product(wp)
        return str(created.id)

    async def test_adds_joints_to_a_node_with_no_prior_assembly(self, client, twin) -> None:
        node_id = await self._make_node(twin)
        async with client:
            resp = await client.patch(
                f"/v1/twin/nodes/{node_id}/assembly-joints",
                json={
                    "joints": [
                        {
                            "name": "shoulder",
                            "type": "revolute",
                            "base": "base_link",
                            "follower": "upper_arm",
                            "axis": [0.0, 0.0, 1.0],
                            "anchor": [0.0, 0.0, 50.0],
                        }
                    ]
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["nodeId"] == node_id
        assert body["assembly"]["parts"] == []
        assert len(body["assembly"]["joints"]) == 1
        assert body["assembly"]["joints"][0]["name"] == "shoulder"
        assert body["assembly"]["joints"][0]["axis"] == [0.0, 0.0, 1.0]

        wp = await twin.get_work_product(UUID(node_id))
        assert wp is not None
        assert wp.metadata["assembly"]["joints"][0]["base"] == "base_link"

    async def test_preserves_existing_parts_while_replacing_joints(self, client, twin) -> None:
        node_id = await self._make_node(
            twin,
            assembly={
                "parts": [{"node_id": "p1", "link_name": "base_link"}],
                "joints": [{"name": "old", "type": "fixed", "base": "a", "follower": "b"}],
            },
        )
        async with client:
            resp = await client.patch(
                f"/v1/twin/nodes/{node_id}/assembly-joints",
                json={
                    "joints": [{"name": "new", "type": "revolute", "base": "a", "follower": "b"}]
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        # Parts untouched -- this route is joints-only.
        assert body["assembly"]["parts"] == [{"node_id": "p1", "link_name": "base_link"}]
        assert [j["name"] for j in body["assembly"]["joints"]] == ["new"]

    async def test_empty_joints_list_clears_all_joints(self, client, twin) -> None:
        node_id = await self._make_node(
            twin,
            assembly={
                "parts": [],
                "joints": [{"name": "old", "type": "fixed", "base": "a", "follower": "b"}],
            },
        )
        async with client:
            resp = await client.patch(
                f"/v1/twin/nodes/{node_id}/assembly-joints", json={"joints": []}
            )
        assert resp.status_code == 200
        assert resp.json()["assembly"]["joints"] == []

    async def test_unknown_node_404s(self, client, twin) -> None:
        async with client:
            resp = await client.patch(
                f"/v1/twin/nodes/{uuid4()}/assembly-joints",
                json={"joints": []},
            )
        assert resp.status_code == 404

    async def test_legacy_boolean_assembly_flag_is_refused_not_clobbered(
        self, client, twin
    ) -> None:
        """MET-745: api_gateway/cad/builder.py's build_assembly() writes a
        bare `assembly: True` on some CAD_MODEL nodes -- a real, documented
        key-name collision with the {parts, joints} shape. This route must
        refuse rather than silently overwrite that flag's meaning."""
        node_id = await self._make_node(twin, assembly=True)  # type: ignore[arg-type]
        async with client:
            resp = await client.patch(
                f"/v1/twin/nodes/{node_id}/assembly-joints",
                json={"joints": []},
            )
        assert resp.status_code == 409

    async def test_limits_and_axis_round_trip(self, client, twin) -> None:
        node_id = await self._make_node(twin)
        async with client:
            resp = await client.patch(
                f"/v1/twin/nodes/{node_id}/assembly-joints",
                json={
                    "joints": [
                        {
                            "name": "elbow",
                            "type": "revolute",
                            "base": "upper_arm",
                            "follower": "forearm",
                            "axis": [1.0, 0.0, 0.0],
                            "anchor": [0.0, 0.0, 120.0],
                            "limits": {"lower": -1.57, "upper": 1.57},
                        }
                    ]
                },
            )
        assert resp.status_code == 200
        joint = resp.json()["assembly"]["joints"][0]
        assert joint["limits"] == {"lower": -1.57, "upper": 1.57}

    async def test_rejects_a_joint_missing_required_fields(self, client, twin) -> None:
        node_id = await self._make_node(twin)
        async with client:
            resp = await client.patch(
                f"/v1/twin/nodes/{node_id}/assembly-joints",
                json={"joints": [{"name": "", "type": "fixed", "base": "a", "follower": "b"}]},
            )
        assert resp.status_code == 422
