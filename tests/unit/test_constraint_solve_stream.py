"""Tier-3 live solve streaming prototype (MET-521)."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_gateway.constraint.routes import router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class TestSolveStream:
    def test_session_then_cascade(self) -> None:
        with _client().websocket_connect("/v1/constraint/solve/stream") as ws:
            session = ws.receive_json()
            assert session["type"] == "session"
            assert session["session_id"]

            ws.send_json({"group_name": "motor_group", "follower": "bracket_group"})
            ws.send_json({"delta": [10, 0, 0]})
            r = ws.receive_json()

            assert r["type"] == "solve"
            groups = {t["group_name"] for t in r["transforms"]}
            assert groups == {"motor_group", "bracket_group"}
            # Follower cascades at half the drag delta.
            follower = next(t for t in r["transforms"] if t["group_name"] == "bracket_group")
            assert follower["delta"][0] == 5.0
            assert r["constraints"][0]["status"] == "satisfied"
            assert "motor_group" in r["recommendation"]
            assert "solve_ms" in r

    def test_violation_on_large_delta(self) -> None:
        with _client().websocket_connect("/v1/constraint/solve/stream") as ws:
            ws.receive_json()  # session
            ws.send_json({"group_name": "g", "delta": [600, 0, 0]})
            r = ws.receive_json()
            assert r["constraints"][0]["status"] == "violated"
            assert r["constraints"][0]["severity"] == "warning"

    def test_handshake_without_delta_is_noop(self) -> None:
        with _client().websocket_connect("/v1/constraint/solve/stream") as ws:
            ws.receive_json()  # session
            ws.send_json({"group_name": "g"})  # no delta → no solve reply
            ws.send_json({"delta": [0, 2, 0]})
            r = ws.receive_json()  # reply is for the delta tick
            assert r["type"] == "solve"
            assert [t["group_name"] for t in r["transforms"]] == ["g"]  # no follower


class TestJointAwareSolveStream:
    """MET-530: when joints are supplied the solver constrains the dragged
    follower to its joint's DOF and streams a `dof` hint."""

    def test_slider_clamps_drag_to_axis(self) -> None:
        with _client().websocket_connect("/v1/constraint/solve/stream") as ws:
            ws.receive_json()  # session
            ws.send_json(
                {
                    "group_name": "carriage",
                    "joints": [
                        {
                            "name": "rail",
                            "type": "slider",
                            "base": "frame",
                            "follower": "carriage",
                            "axis": [1, 0, 0],
                        }
                    ],
                }
            )
            ws.send_json({"delta": [10, 7, 3]})  # off-axis drag
            r = ws.receive_json()
            t = next(t for t in r["transforms"] if t["group_name"] == "carriage")
            # Clamped to the X slide axis.
            assert t["delta"] == [10.0, 0.0, 0.0]
            assert r["dof"]["translation_axes"] == [[1.0, 0.0, 0.0]]
            assert r["dof"]["rotation_axes"] == []
            # A joint-status constraint is reported.
            assert any(c.get("type") == "joint" for c in r["constraints"])

    def test_revolute_produces_rotation_with_grab_point(self) -> None:
        with _client().websocket_connect("/v1/constraint/solve/stream") as ws:
            ws.receive_json()
            ws.send_json(
                {
                    "group_name": "arm",
                    "joints": [
                        {
                            "name": "hinge",
                            "type": "revolute",
                            "base": "body",
                            "follower": "arm",
                            "axis": [0, 0, 1],
                            "anchor": [0, 0, 0],
                        }
                    ],
                }
            )
            ws.send_json({"delta": [0, 1, 0], "grab_point": [10, 0, 0]})
            r = ws.receive_json()
            t = next(t for t in r["transforms"] if t["group_name"] == "arm")
            assert "rotation" in t
            assert t["rotation"]["angle_deg"] > 0
            assert r["dof"]["rotation_axes"] == [[0.0, 0.0, 1.0]]

    def test_unjointed_group_moves_freely_no_dof(self) -> None:
        with _client().websocket_connect("/v1/constraint/solve/stream") as ws:
            ws.receive_json()
            ws.send_json(
                {
                    "group_name": "loose",
                    "joints": [
                        {"type": "slider", "base": "a", "follower": "other", "axis": [1, 0, 0]}
                    ],
                }
            )
            ws.send_json({"delta": [4, 5, 6]})
            r = ws.receive_json()
            t = next(t for t in r["transforms"] if t["group_name"] == "loose")
            assert t["delta"] == [4.0, 5.0, 6.0]  # free move
            assert "dof" not in r  # no joint governs the dragged group
