"""Unit tests for the online session-trajectory scorer (MET-572). Network-free."""

from __future__ import annotations

import sys
from pathlib import Path

_EVALS = Path(__file__).resolve().parents[2] / "evals"
sys.path.insert(0, str(_EVALS))

from score_sessions import (  # noqa: E402
    score_session,
    session_to_turn,
    summarize_sessions,
)


def _action(tool: str, args: dict | None = None, status: str = "ok") -> dict:
    return {
        "type": "action",
        "message": tool,
        "data": {"tool_id": tool, "status": status, "args": args or {}},
    }


def _session(events: list[dict], sid: str = "s1") -> dict:
    return {
        "id": sid,
        "agent_code": "mcp",
        "status": "completed",
        "started_at": "2026-08-03T00:00:00Z",
        "events": events,
    }


def test_action_events_become_steps_and_others_are_ignored() -> None:
    turn = session_to_turn(
        _session(
            [
                {"type": "thought", "message": "thinking"},
                _action("freecad.create_primitive", {"kind": "box"}),
                {"type": "decision", "message": "chose box"},
            ]
        )
    )
    assert len(turn["steps"]) == 1
    assert turn["steps"][0]["tool"] == "freecad.create_primitive"
    assert turn["steps"][0]["error"] is None


def test_non_ok_status_becomes_step_error() -> None:
    turn = session_to_turn(_session([_action("kicad.run_erc", status="error")]))
    assert turn["steps"][0]["error"] == "status=error"


def test_actionless_session_is_skipped() -> None:
    assert score_session(_session([{"type": "thought", "message": "hi"}])) is None


def test_clean_trajectory_scores_full() -> None:
    row = score_session(_session([_action("t", {"a": 1}), _action("t", {"a": 2})]))
    assert row is not None
    assert row["score"] == 1.0
    assert row["n_actions"] == 2
    # Reply-based check is dropped for captured traces.
    assert "big_observation_survived" not in row["checks"]


def test_duplicate_identical_calls_are_flagged() -> None:
    row = score_session(_session([_action("t", {"a": 1}), _action("t", {"a": 1})]))
    assert row is not None
    assert not row["checks"]["no_duplicate_identical_calls"]
    assert row["score"] < 1.0


def test_identical_retry_of_failure_is_flagged() -> None:
    row = score_session(_session([_action("t", {"a": 1}, status="error"), _action("t", {"a": 1})]))
    assert row is not None
    assert not row["checks"]["no_repeated_failing_call"]


def test_summary_aggregates_and_names_failing_sessions() -> None:
    clean = score_session(_session([_action("t", {"a": 1})], sid="ok"))
    dup = score_session(_session([_action("t", {"a": 1}), _action("t", {"a": 1})], sid="dup"))
    assert clean and dup
    summary = summarize_sessions([clean, dup])
    assert summary["sessions_scored"] == 2
    assert summary["total_actions"] == 3
    assert summary["sessions_with_failing_checks"] == ["dup"]


def test_summary_empty() -> None:
    assert summarize_sessions([])["avg_score"] is None
