"""Unit tests for the chat context-window rubric (MET-570). Network-free."""

from __future__ import annotations

import sys
from pathlib import Path

_EVALS = Path(__file__).resolve().parents[2] / "evals"
sys.path.insert(0, str(_EVALS))

from chat_rubric_common import FALLBACK_ANSWER  # noqa: E402
from chat_window_rubric import evaluate_chat_window  # noqa: E402


def _stats(used: int = 1000, window: int = 128000, included: int = 5, available: int = 5) -> dict:
    return {
        "used": used,
        "window": window,
        "components": [
            {
                "key": "history",
                "label": "history",
                "items_included": included,
                "items_available": available,
            },
        ],
    }


def _turn(reply: str = "ok", stats: dict | None = None, error: bool = False) -> dict:
    return {"reply": reply, "context_stats": stats, "reply_error": error, "steps": [], "checks": []}


def test_healthy_short_conversation_passes() -> None:
    turns = [_turn(stats=_stats()), _turn(stats=_stats())]
    assert evaluate_chat_window(turns) == {
        "stats_observed": True,
        "window_never_exceeded": True,
        "history_trim_reported": True,
        "late_turns_completed": True,
    }


def test_no_stats_at_all_is_flagged() -> None:
    checks = evaluate_chat_window([_turn(), _turn()])
    assert not checks["stats_observed"]
    assert not checks["window_never_exceeded"]


def test_usage_at_or_over_window_fails() -> None:
    turns = [_turn(stats=_stats(used=130000, window=128000))]
    assert not evaluate_chat_window(turns)["window_never_exceeded"]


def test_untrimmed_over_limit_history_means_lying_meter() -> None:
    # 30 turns exist and the meter claims all 30 went in: with the 20-turn
    # slice that telemetry would be false.
    turns = [_turn(stats=_stats(included=30, available=30))]
    assert not evaluate_chat_window(turns)["history_trim_reported"]


def test_trimmed_over_limit_history_is_honest() -> None:
    turns = [_turn(stats=_stats(included=20, available=30))]
    assert evaluate_chat_window(turns)["history_trim_reported"]


def test_fallback_in_final_turns_fails_late_completion() -> None:
    turns = [_turn() for _ in range(6)] + [_turn(reply=FALLBACK_ANSWER)]
    assert not evaluate_chat_window(turns)["late_turns_completed"]


def test_error_in_final_turns_fails_late_completion() -> None:
    turns = [_turn() for _ in range(6)] + [_turn(error=True)]
    assert not evaluate_chat_window(turns)["late_turns_completed"]
