"""Unit tests for the chat tool-trajectory rubric (MET-570). Network-free."""

from __future__ import annotations

import sys
from pathlib import Path

_EVALS = Path(__file__).resolve().parents[2] / "evals"
sys.path.insert(0, str(_EVALS))

from chat_rubric_common import FALLBACK_ANSWER  # noqa: E402
from chat_tooluse_rubric import evaluate_chat_tooluse  # noqa: E402


def _step(tool: str = "t", args: dict | None = None, error: str | None = None) -> dict:
    return {"tool": tool, "arguments": args or {}, "error": error}


def _turn(steps: list | None = None, reply: str = "ok", tag: str | None = None) -> dict:
    return {"steps": steps or [], "reply": reply, "tag": tag, "checks": [], "reply_error": False}


def test_clean_trajectory_passes() -> None:
    turns = [_turn(steps=[_step(args={"a": 1}), _step(args={"a": 2})])]
    assert evaluate_chat_tooluse(turns) == {
        "no_duplicate_identical_calls": True,
        "no_repeated_failing_call": True,
        "tool_errors_bounded": True,
        "big_observation_survived": True,
    }


def test_identical_repeat_flags_duplicate() -> None:
    turns = [_turn(steps=[_step(args={"a": 1}), _step(args={"a": 1})])]
    checks = evaluate_chat_tooluse(turns)
    assert not checks["no_duplicate_identical_calls"]
    # The first call succeeded, so this is a duplicate, not a failing retry.
    assert checks["no_repeated_failing_call"]


def test_retrying_a_failed_call_with_same_args_flags_refail() -> None:
    turns = [_turn(steps=[_step(args={"a": 1}, error="boom"), _step(args={"a": 1})])]
    assert not evaluate_chat_tooluse(turns)["no_repeated_failing_call"]


def test_duplicates_across_turns_are_allowed() -> None:
    # Re-asking the same tool in a *later* turn is legitimate reuse.
    turns = [_turn(steps=[_step(args={"a": 1})]), _turn(steps=[_step(args={"a": 1})])]
    assert evaluate_chat_tooluse(turns)["no_duplicate_identical_calls"]


def test_error_rate_above_third_fails() -> None:
    steps = [_step(args={"a": 1}, error="x"), _step(args={"a": 2})]
    assert not evaluate_chat_tooluse([_turn(steps=steps)])["tool_errors_bounded"]


def test_no_tool_calls_is_bounded() -> None:
    assert evaluate_chat_tooluse([_turn()])["tool_errors_bounded"]


def test_invalid_reply_recovery_is_not_a_tool_error() -> None:
    """A ReAct parse-recovery pseudo-step ("(invalid_reply)") is protocol
    noise, not tool indiscipline — it must not trip the error-rate bound
    (live-caught: one self-corrected hiccup failed an otherwise-perfect run)."""
    turns = [
        _turn(
            steps=[
                _step(tool="(invalid_reply)", error="malformed reply"),
                _step(tool="mcp_twin_query_cypher", args={"q": "MATCH ..."}),
                _step(tool="mcp_twin_record_decision", args={"t": "x"}),
            ]
        )
    ]
    assert evaluate_chat_tooluse(turns)["tool_errors_bounded"]


def test_bigobs_turn_with_fallback_reply_fails() -> None:
    turns = [_turn(reply=FALLBACK_ANSWER, tag="bigobs")]
    assert not evaluate_chat_tooluse(turns)["big_observation_survived"]


def test_no_bigobs_turns_is_vacuously_true() -> None:
    assert evaluate_chat_tooluse([_turn()])["big_observation_survived"]
