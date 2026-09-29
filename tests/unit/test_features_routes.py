"""Unit tests for POST /v1/features/generate (FORGE-269, gap G-D1)."""

from __future__ import annotations

import base64

import pytest
from httpx import ASGITransport, AsyncClient

from skill_registry.mcp_bridge import InMemoryMcpBridge
from twin_core.api import InMemoryTwinAPI


def _register_freecad_session_tools(mcp: InMemoryMcpBridge) -> None:
    mcp.register_tool("freecad.open_session", capability="cad_session")
    mcp.register_tool_response("freecad.open_session", {"session_id": "sess-1"})
    mcp.register_tool("freecad.close_session", capability="cad_session")
    mcp.register_tool_response("freecad.close_session", {})
    for tool_id in (
        "freecad.create_body",
        "freecad.create_sketch",
        "freecad.pad_sketch",
        "freecad.pocket_sketch",
        "freecad.polar_pattern",
    ):
        mcp.register_tool(tool_id, capability="cad_author")
        mcp.register_tool_response(tool_id, {"obj_id": f"{tool_id.split('.')[1]}_1"})
    mcp.register_tool("freecad.measure", capability="cad_inspect")
    mcp.register_tool_response(
        "freecad.measure",
        {
            "volume_mm3": 6000.0,
            "surface_area_mm2": 2200.0,
            "bounding_box": {
                "min_x": -30.0,
                "min_y": -20.0,
                "min_z": 0.0,
                "max_x": 30.0,
                "max_y": 20.0,
                "max_z": 5.0,
            },
        },
    )
    mcp.register_tool("freecad.export_model", capability="cad_export")
    mcp.register_tool_response(
        "freecad.export_model",
        {"step_base64": base64.b64encode(b"ISO-10303-21;").decode("ascii")},
    )


@pytest.fixture
def twin() -> InMemoryTwinAPI:
    return InMemoryTwinAPI.create()


@pytest.fixture
def mcp() -> InMemoryMcpBridge:
    bridge = InMemoryMcpBridge()
    _register_freecad_session_tools(bridge)
    return bridge


@pytest.fixture
def app(twin, mcp):
    from fastapi import FastAPI

    from api_gateway.features.routes import init_twin, router

    app = FastAPI()
    app.include_router(router)
    app.state.mcp_bridge = mcp
    init_twin(twin)
    yield app
    init_twin(InMemoryTwinAPI.create())


@pytest.fixture
def client(app):
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


class TestGenerateFeatureRoute:
    async def test_generates_rib(self, client, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        async with client:
            resp = await client.post(
                "/v1/features/generate",
                json={
                    "name": "Test Rib",
                    "feature": {
                        "feature_type": "rib",
                        "length_mm": 30.0,
                        "height_mm": 20.0,
                        "thickness_mm": 3.0,
                    },
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["feature_type"] == "rib"
        assert body["entity_count"] == 3

    async def test_generates_bolt_pattern(self, client, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        async with client:
            resp = await client.post(
                "/v1/features/generate",
                json={
                    "name": "Test Bolt Pattern",
                    "feature": {
                        "feature_type": "bolt_pattern",
                        "plate_length_mm": 60.0,
                        "plate_width_mm": 40.0,
                        "plate_thickness_mm": 5.0,
                        "hole_diameter_mm": 4.0,
                        "hole_count": 4,
                        "pattern_radius_mm": 15.0,
                    },
                },
            )
        assert resp.status_code == 200
        assert resp.json()["entity_count"] == 6

    async def test_invalid_feature_params_400s(self, client) -> None:
        async with client:
            resp = await client.post(
                "/v1/features/generate",
                json={
                    "name": "Bad Bolt Pattern",
                    "feature": {
                        "feature_type": "bolt_pattern",
                        "plate_length_mm": 20.0,
                        "plate_width_mm": 20.0,
                        "plate_thickness_mm": 5.0,
                        "hole_diameter_mm": 4.0,
                        "hole_count": 4,
                        "pattern_radius_mm": 15.0,
                    },
                },
            )
        assert resp.status_code == 400

    async def test_unknown_feature_type_400s(self, client) -> None:
        async with client:
            resp = await client.post(
                "/v1/features/generate",
                json={"name": "Bogus", "feature": {"feature_type": "gear_stage"}},
            )
        assert resp.status_code == 400

    async def test_mcp_bridge_unavailable_503s(self, twin) -> None:
        from fastapi import FastAPI

        from api_gateway.features.routes import init_twin, router

        app = FastAPI()
        app.include_router(router)
        init_twin(twin)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/features/generate",
                json={
                    "name": "Test Rib",
                    "feature": {
                        "feature_type": "rib",
                        "length_mm": 30.0,
                        "height_mm": 20.0,
                        "thickness_mm": 3.0,
                    },
                },
            )
        assert resp.status_code == 503
        init_twin(InMemoryTwinAPI.create())
