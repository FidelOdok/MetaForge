"""Tests for the robot joint-loads gateway route (FORGE-283).

Follows the same fake-bridge pattern as test_cad_export_routes.py: a
lightweight FastAPI app with just this router, and a fake McpBridge that
records calls and returns a canned calculix.compute_joint_loads response.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_gateway.robot_loads.routes import router


class _FakeBridge:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail = fail

    async def invoke(
        self, tool_id: str, params: dict[str, Any], timeout: int | None = None
    ) -> dict[str, Any]:
        self.calls.append((tool_id, params))
        if self.fail:
            raise RuntimeError(f"{tool_id} exploded")
        assert tool_id == "calculix.compute_joint_loads"
        loads = [
            {
                "joint_name": joint["name"],
                "supported_mass_kg": 2.0,
                "reaction_force_n": [0.0, 0.0, 19.6],
                "reaction_moment_n_mm": [0.0, -1962.0, 0.0],
            }
            for joint in params["joints"]
        ]
        return {"loads": loads, "worst_joint": loads[0]}


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _patch_bridge(monkeypatch: pytest.MonkeyPatch, bridge: _FakeBridge) -> None:
    monkeypatch.setattr("api_gateway.chat.routes.get_mcp_bridge", lambda: bridge)


_BODY = {
    "links": [{"name": "link0", "com_world_mm": [100.0, 0.0, 0.0], "mass_kg": 2.0}],
    "joints": [{"name": "j0", "position_world_mm": [0.0, 0.0, 0.0]}],
}


class TestComputeJointLoadsRoute:
    def test_success(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        bridge = _FakeBridge()
        _patch_bridge(monkeypatch, bridge)

        response = client.post("/v1/robot/joint-loads", json=_BODY)

        assert response.status_code == 200
        data = response.json()
        assert data["worst_joint"]["joint_name"] == "j0"
        assert len(data["loads"]) == 1
        assert bridge.calls[0][0] == "calculix.compute_joint_loads"
        assert bridge.calls[0][1]["payload_mass_kg"] == 0.0

    def test_with_payload(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        bridge = _FakeBridge()
        _patch_bridge(monkeypatch, bridge)

        body = {
            **_BODY,
            "payload_mass_kg": 5.0,
            "payload_position_world_mm": [200.0, 0.0, 0.0],
        }
        response = client.post("/v1/robot/joint-loads", json=body)

        assert response.status_code == 200
        assert bridge.calls[0][1]["payload_mass_kg"] == 5.0
        assert bridge.calls[0][1]["payload_position_world_mm"] == [200.0, 0.0, 0.0]

    def test_tool_failure_returns_502(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        bridge = _FakeBridge(fail=True)
        _patch_bridge(monkeypatch, bridge)

        response = client.post("/v1/robot/joint-loads", json=_BODY)

        assert response.status_code == 502

    def test_missing_links_returns_422(self, client: TestClient) -> None:
        response = client.post("/v1/robot/joint-loads", json={"joints": []})
        assert response.status_code == 422
