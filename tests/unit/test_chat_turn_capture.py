"""Chat-turn Action-Observation capture (MET-594). Network-free.

The key contract: captured events carry the exact ``{tool_id, status, args}``
shape the MCP sidecar's Layer-A capture uses, so ``score_sessions``' rubric
replays chat trajectories with no adapter.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from api_gateway.chat.turn_capture import (
    capture_step,
    capture_turn_done,
    init_turn_capture,
)

_EVALS = Path(__file__).resolve().parents[2] / "evals"
sys.path.insert(0, str(_EVALS))


class _FakeStore:
    def __init__(self, boom: bool = False) -> None:
        self.sessions: list[dict[str, Any]] = []
        self.events: list[tuple[str, str, str, dict]] = []
        self._boom = boom

    async def create_session(self, **kwargs: Any) -> Any:
        if self._boom:
            raise RuntimeError("db down")
        self.sessions.append(kwargs)
        return SimpleNamespace(id=f"s-{len(self.sessions)}")

    async def append_event(
        self, session_id: str, *, type: str, message: str, data: dict | None = None
    ) -> tuple[str, int]:
        self.events.append((session_id, type, message, data or {}))
        return ("e", len(self.events))


def _step(tool: str | None = "mcp_twin_get_node", error: str | None = None) -> dict[str, Any]:
    return {
        "index": 0,
        "thought": "checking the twin",
        "tool": tool,
        "arguments": {"node_id": "n1"},
        "observation": {"node": {"id": "n1"}},
        "error": error,
        "final": False,
    }


@pytest.mark.asyncio
async def test_capture_creates_one_session_per_thread_and_scores_shape() -> None:
    store = _FakeStore()
    init_turn_capture(store)
    try:
        await capture_step("t1", "p1", _step())
        await capture_step("t1", "p1", _step(error="boom"))
        await capture_turn_done("t1", "p1", status="completed", steps=2)
        await capture_step("t2", None, _step())

        assert len(store.sessions) == 2  # one per thread, reused within
        assert store.sessions[0]["agent_code"] == "chat-harness"
        assert store.sessions[0]["project_id"] == "p1"

        sid, etype, message, data = store.events[0]
        assert etype == "action" and message == "mcp_twin_get_node"
        assert data["tool_id"] == "mcp_twin_get_node"
        assert data["status"] == "ok" and data["args"] == {"node_id": "n1"}
        assert "thought" in data and "observation_digest" in data
        assert store.events[1][3]["status"] == "error"
        assert store.events[2][1] == "observation"  # turn marker
        assert store.events[2][3] == {"status": "completed", "steps": 2}
    finally:
        init_turn_capture(None)


@pytest.mark.asyncio
async def test_final_reasoning_steps_are_not_actions() -> None:
    store = _FakeStore()
    init_turn_capture(store)
    try:
        await capture_step("t1", None, {"index": 3, "thought": "done", "tool": None, "final": True})
        assert store.events == [] and store.sessions == []
    finally:
        init_turn_capture(None)


@pytest.mark.asyncio
async def test_capture_is_fail_open() -> None:
    init_turn_capture(_FakeStore(boom=True))
    try:
        await capture_step("t1", None, _step())  # must not raise
        await capture_turn_done("t1", None, status="completed", steps=1)
    finally:
        init_turn_capture(None)


@pytest.mark.asyncio
async def test_disabled_capture_is_noop() -> None:
    init_turn_capture(None)
    await capture_step("t1", None, _step())
    await capture_turn_done("t1", None, status="completed", steps=1)


@pytest.mark.asyncio
async def test_captured_events_replay_through_score_sessions() -> None:
    """The MET-594 payoff: a captured chat trajectory scores through the
    online-eval rubric with no adapter."""
    from score_sessions import score_session

    store = _FakeStore()
    init_turn_capture(store)
    try:
        await capture_step("t1", "p1", _step())
        await capture_step("t1", "p1", _step())  # duplicate identical call
        session = {
            "id": "s-1",
            "agent_code": "chat-harness",
            "status": "running",
            "project_id": "p1",
            "events": [
                {"type": etype, "message": msg, "data": data}
                for _, etype, msg, data in store.events
            ],
        }
        row = score_session(session)
        assert row is not None and row["n_actions"] == 2
        assert row["checks"]["no_duplicate_identical_calls"] is False  # caught!
    finally:
        init_turn_capture(None)
