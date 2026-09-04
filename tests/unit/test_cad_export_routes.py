"""Tests for the cadquery robotics-sim export routes (MET-719).

Follows the fake-bridge pattern from ``tests/unit/test_cad_assembly.py``:
a lightweight FastAPI app with just this router, and a fake ``McpBridge``
that records calls and returns canned tool responses shaped like the real
``cadquery``/``twin`` tools' output schemas.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_gateway.cad_export.routes import router


class _FakeBridge:
    """Fake MCP bridge: stages a part to a fixed path, echoes export params
    back the way the real cadquery tools would (they write to and return
    exactly the ``output_path`` they were given)."""

    def __init__(self, *, fail_stage: bool = False, fail_export_tool: str | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail_stage = fail_stage
        self.fail_export_tool = fail_export_tool

    async def invoke(
        self, tool_id: str, params: dict[str, Any], timeout: int | None = None
    ) -> dict[str, Any]:
        self.calls.append((tool_id, params))

        if tool_id == "twin.stage_work_product_file":
            if self.fail_stage:
                raise RuntimeError("work product not found")
            node_id = params["node_id"]
            return {
                "node_id": node_id,
                "file_path": f"/workspace/_staged_work_products/{node_id}/part.step",
                "filename": "part.step",
                "size_bytes": 123,
                "content_hash": "deadbeef",
                "format": "step",
            }

        if self.fail_export_tool == tool_id:
            raise RuntimeError(f"{tool_id} exploded")

        if tool_id == "cadquery.export_urdf":
            out = params["output_path"]
            mesh = str(Path(out).with_suffix(f".{params.get('mesh_format', 'stl')}"))
            return {
                "output_file": out,
                "mesh_file": mesh,
                "link_name": params.get("link_name", "base_link"),
                "density_kg_m3": params.get("density_kg_m3") or 2700.0,
                "mass_kg": 0.512,
                "center_of_mass_m": {"x": 0.0, "y": 0.0, "z": 0.01},
                "inertia_kgm2": {
                    "ixx": 1e-6,
                    "ixy": 0.0,
                    "ixz": 0.0,
                    "iyy": 1e-6,
                    "iyz": 0.0,
                    "izz": 1e-6,
                },
            }

        if tool_id == "cadquery.export_urdf_assembly":
            out = params["output_path"]
            mesh_dir = Path(out).parent
            mesh_files = [str(mesh_dir / f"{p['link_name']}.stl") for p in params["parts"]]
            return {
                "output_file": out,
                "mesh_files": mesh_files,
                "robot_name": params.get("robot_name", "robot"),
                "link_names": [p["link_name"] for p in params["parts"]],
                "joint_names": [j["name"] for j in params["joints"]],
            }

        if tool_id == "cadquery.generate_ros2_launch":
            return {
                "output_file": params["output_path"],
                "robot_name": params["robot_name"],
                "default_urdf_path": params["default_urdf_path"],
            }

        if tool_id == "freecad.describe_session":
            if params["session_id"] == "missing":
                raise RuntimeError("unknown session")
            return {
                "session_id": params["session_id"],
                "name": "my_assembly",
                "object_count": 2,
                "objects": [
                    {"obj_id": "o1", "kind": "part", "name": "base", "order": 0},
                    {"obj_id": "o2", "kind": "part", "name": "arm", "order": 1},
                ],
            }

        if tool_id == "freecad.list_joints":
            return {
                "joints": [
                    {
                        "name": "base-arm",
                        "type": "revolute",
                        "base": "base",
                        "follower": "arm",
                        "axis": [0.0, 0.0, 1.0],
                        "anchor": [0.0, 0.0, 10.0],
                    }
                ]
            }

        raise AssertionError(f"unexpected tool call in test: {tool_id}")


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _patch_bridge(monkeypatch: pytest.MonkeyPatch, bridge: _FakeBridge) -> None:
    monkeypatch.setattr("api_gateway.chat.routes.get_mcp_bridge", lambda: bridge)


def test_export_urdf_stages_part_and_returns_download_links(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ADAPTER_WORKSPACE_DIR", str(tmp_path))
    bridge = _FakeBridge()
    _patch_bridge(monkeypatch, bridge)

    resp = client.post("/v1/cad-export/urdf", json={"node_id": "wp-1", "link_name": "arm_base"})

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["link_name"] == "arm_base"
    assert body["mass_kg"] == pytest.approx(0.512)
    assert body["output_file"]["filename"] == "model.urdf"
    assert body["output_file"]["download_url"].startswith("/v1/cad-export/download/")
    assert body["mesh_file"]["filename"] == "model.stl"

    # The part was resolved via the Twin bridge before the export tool ran.
    tool_ids = [c[0] for c in bridge.calls]
    assert tool_ids == ["twin.stage_work_product_file", "cadquery.export_urdf"]
    stage_args = bridge.calls[0][1]
    assert stage_args["node_id"] == "wp-1"
    export_args = bridge.calls[1][1]
    assert export_args["input_file"] == "/workspace/_staged_work_products/wp-1/part.step"
    assert export_args["link_name"] == "arm_base"


def test_export_urdf_xacro_flag_changes_output_extension(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ADAPTER_WORKSPACE_DIR", str(tmp_path))
    bridge = _FakeBridge()
    _patch_bridge(monkeypatch, bridge)

    resp = client.post("/v1/cad-export/urdf", json={"node_id": "wp-1", "xacro": True})

    assert resp.status_code == 201, resp.text
    assert resp.json()["output_file"]["filename"] == "model.xacro"


def test_export_urdf_assembly_stages_every_part(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ADAPTER_WORKSPACE_DIR", str(tmp_path))
    bridge = _FakeBridge()
    _patch_bridge(monkeypatch, bridge)

    resp = client.post(
        "/v1/cad-export/urdf-assembly",
        json={
            "parts": [
                {"node_id": "wp-a", "link_name": "base"},
                {"node_id": "wp-b", "link_name": "arm"},
            ],
            "joints": [
                {
                    "name": "shoulder",
                    "type": "revolute",
                    "base": "base",
                    "follower": "arm",
                    "axis": [0, 0, 1],
                    "anchor": [0, 0, 10],
                }
            ],
            "robot_name": "my_robot",
        },
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["robot_name"] == "my_robot"
    assert body["link_names"] == ["base", "arm"]
    assert body["joint_names"] == ["shoulder"]
    assert len(body["mesh_files"]) == 2
    assert all(m["download_url"].startswith("/v1/cad-export/download/") for m in body["mesh_files"])

    tool_ids = [c[0] for c in bridge.calls]
    assert tool_ids == [
        "twin.stage_work_product_file",
        "twin.stage_work_product_file",
        "cadquery.export_urdf_assembly",
    ]
    export_args = bridge.calls[-1][1]
    assert [p["input_file"] for p in export_args["parts"]] == [
        "/workspace/_staged_work_products/wp-a/part.step",
        "/workspace/_staged_work_products/wp-b/part.step",
    ]
    assert export_args["joints"][0]["type"] == "revolute"


def test_export_urdf_assembly_rejects_empty_parts(client: TestClient) -> None:
    resp = client.post("/v1/cad-export/urdf-assembly", json={"parts": [], "joints": []})
    assert resp.status_code == 422


def test_generate_ros2_launch_does_not_touch_twin(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ADAPTER_WORKSPACE_DIR", str(tmp_path))
    bridge = _FakeBridge()
    _patch_bridge(monkeypatch, bridge)

    resp = client.post(
        "/v1/cad-export/ros2-launch",
        json={"robot_name": "arm", "default_urdf_path": "/v1/cad-export/download/abc/model.urdf"},
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["robot_name"] == "arm"
    assert body["output_file"]["filename"] == "arm.launch.py"
    assert [c[0] for c in bridge.calls] == ["cadquery.generate_ros2_launch"]


def test_export_urdf_returns_404_when_work_product_missing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ADAPTER_WORKSPACE_DIR", str(tmp_path))
    bridge = _FakeBridge()

    async def _empty_stage(
        tool_id: str, params: dict[str, Any], timeout: int | None = None
    ) -> dict[str, Any]:
        return {"node_id": params["node_id"], "file_path": ""}

    bridge.invoke = _empty_stage  # type: ignore[method-assign]
    _patch_bridge(monkeypatch, bridge)

    resp = client.post("/v1/cad-export/urdf", json={"node_id": "missing-wp"})
    assert resp.status_code == 404


def test_export_urdf_returns_502_when_export_tool_fails(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ADAPTER_WORKSPACE_DIR", str(tmp_path))
    bridge = _FakeBridge(fail_export_tool="cadquery.export_urdf")
    _patch_bridge(monkeypatch, bridge)

    resp = client.post("/v1/cad-export/urdf", json={"node_id": "wp-1"})
    assert resp.status_code == 502
    assert "cadquery.export_urdf" in resp.json()["detail"]


def test_download_export_file_round_trip(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ADAPTER_WORKSPACE_DIR", str(tmp_path))
    export_id = "11111111222233334444555566667777"
    export_dir = tmp_path / "_cad_exports" / export_id
    export_dir.mkdir(parents=True)
    (export_dir / "model.urdf").write_text("<robot/>")

    resp = client.get(f"/v1/cad-export/download/{export_id}/model.urdf")

    assert resp.status_code == 200
    assert resp.text == "<robot/>"


def test_download_export_file_404_when_missing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ADAPTER_WORKSPACE_DIR", str(tmp_path))
    resp = client.get("/v1/cad-export/download/11111111222233334444555566667777/model.urdf")
    assert resp.status_code == 404


def test_download_export_file_rejects_path_traversal(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ADAPTER_WORKSPACE_DIR", str(tmp_path))
    export_id = "11111111222233334444555566667777"
    resp = client.get(f"/v1/cad-export/download/{export_id}/..%2F..%2Fetc%2Fpasswd")
    assert resp.status_code in (400, 404)


def test_download_export_file_rejects_invalid_export_id(client: TestClient) -> None:
    resp = client.get("/v1/cad-export/download/not-a-uuid/model.urdf")
    assert resp.status_code == 400


def test_get_session_summary_lists_objects(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    bridge = _FakeBridge()
    _patch_bridge(monkeypatch, bridge)

    resp = client.get("/v1/cad-export/sessions/sess-1")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["session_id"] == "sess-1"
    assert body["object_count"] == 2
    assert [o["name"] for o in body["objects"]] == ["base", "arm"]
    assert bridge.calls == [("freecad.describe_session", {"session_id": "sess-1"})]


def test_get_session_summary_502_when_session_missing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    bridge = _FakeBridge()
    _patch_bridge(monkeypatch, bridge)

    resp = client.get("/v1/cad-export/sessions/missing")
    assert resp.status_code == 502


def test_get_session_joints_returns_recorded_joints(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    bridge = _FakeBridge()
    _patch_bridge(monkeypatch, bridge)

    resp = client.get("/v1/cad-export/sessions/sess-1/joints")

    assert resp.status_code == 200, resp.text
    joints = resp.json()["joints"]
    assert len(joints) == 1
    assert joints[0]["base"] == "base"
    assert joints[0]["follower"] == "arm"
    assert joints[0]["type"] == "revolute"
    assert bridge.calls == [("freecad.list_joints", {"session_id": "sess-1"})]
