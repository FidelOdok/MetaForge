"""Unit tests for the chat memory rubric (MET-570). Network-free."""

from __future__ import annotations

import sys
from pathlib import Path

_EVALS = Path(__file__).resolve().parents[2] / "evals"
sys.path.insert(0, str(_EVALS))

from chat_memory_rubric import evaluate_chat_memory, score_chat_memory  # noqa: E402
from chat_rubric_common import FALLBACK_ANSWER  # noqa: E402


def _turn(reply: str = "ok", checks: list | None = None, error: bool = False) -> dict:
    return {"reply": reply, "checks": checks or [], "reply_error": error, "steps": []}


_PROBE = [{"id": "x", "type": "reply_contains", "pattern": "x"}]


def test_healthy_conversation_passes() -> None:
    turns = [_turn(), _turn(reply="the answer", checks=_PROBE)]
    assert evaluate_chat_memory(turns) == {
        "probe_turns_replied": True,
        "no_error_turns": True,
    }


def test_fallback_reply_on_probe_turn_fails() -> None:
    turns = [_turn(), _turn(reply=FALLBACK_ANSWER, checks=_PROBE)]
    assert not evaluate_chat_memory(turns)["probe_turns_replied"]


def test_no_probe_turns_is_a_failure_not_a_free_pass() -> None:
    assert not evaluate_chat_memory([_turn(), _turn()])["probe_turns_replied"]


def test_errored_turn_flips_no_error_turns() -> None:
    turns = [_turn(), _turn(error=True)]
    assert not evaluate_chat_memory(turns)["no_error_turns"]


def test_score_shape() -> None:
    out = score_chat_memory([_turn(reply="a", checks=_PROBE)])
    assert set(out) == {"score", "checks"}
    assert out["score"] == 1.0
