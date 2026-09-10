"""Runner-side gateway actions: find_run_id + approve_run (MET-587 e2e turn).

Network-free — the runner module is stdlib-only and safe to import; the
approval dance is exercised against a scripted `api` transport.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_EVALS = Path(__file__).resolve().parents[2] / "evals"
sys.path.insert(0, str(_EVALS))

import run_chat_scenarios as runner  # noqa: E402


def _turn(observation: Any = None, reply: str = "") -> dict:
    steps = (
        [{"tool": "mcp_run_start_design_flow", "observation": observation}] if observation else []
    )
    return {"steps": steps, "reply": reply}


def test_find_run_id_prefers_newest_observation() -> None:
    turns = [
        _turn(observation={"run_id": "run_00000000aaaa"}),
        _turn(observation="{'run_id': 'run_11111111bbbb', 'status': 'running'}"),
    ]
    assert runner.find_run_id(turns) == "run_11111111bbbb"


def test_find_run_id_falls_back_to_reply_and_none() -> None:
    assert (
        runner.find_run_id([_turn(reply="Started run `run_22222222cccc`.")]) == "run_22222222cccc"
    )
    assert runner.find_run_id([_turn(reply="no id here")]) is None


def test_approve_run_happy_path(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []
    statuses = iter(["running", "awaiting_approval", "running"])

    def fake_api(base, method, path, body=None, timeout=30):
        calls.append((method, path))
        if method == "GET":
            return {"status": next(statuses)}
        assert body == {"decision": "approve"}
        return {}

    monkeypatch.setattr(runner, "api", fake_api)
    monkeypatch.setattr(runner.time, "sleep", lambda s: None)
    out = runner.approve_run_action("http://gw", [_turn(reply="run_33333333dddd")], timeout_s=30)
    assert out["action_ok"] is True
    assert out["status_before"] == "awaiting_approval" and out["status_after"] == "running"
    assert ("POST", "/v1/runs/run_33333333dddd/approval") in calls


def test_approve_run_reports_never_paused(monkeypatch) -> None:
    monkeypatch.setattr(runner, "api", lambda *a, **k: {"status": "failed"})
    out = runner.approve_run_action("http://gw", [_turn(reply="run_44444444eeee")], timeout_s=5)
    assert out["action_ok"] is False
    assert "never paused" in out["action_detail"]


def test_approve_run_without_run_id_fails_cleanly() -> None:
    out = runner.approve_run_action("http://gw", [_turn(reply="nothing")], timeout_s=1)
    assert out["action_ok"] is False and "no run id" in out["action_detail"]
