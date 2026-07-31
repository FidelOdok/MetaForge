#!/usr/bin/env python3
"""Context-window behavior rubric for the chat context evals (MET-570).

Scores the ``context.stats`` telemetry the gateway emits per turn plus the
conversation's tail health. Today history is a hard 20-turn slice
(``_HISTORY_LIMIT`` in ``api_gateway/chat/routes.py``) and nothing enforces the
window — these checks pin the telemetry's honesty (trimming is *reported*,
usage stays under the window) and that long conversations still finish, while
the recall-past-the-slice checks live in the scenarios as ``expected_today``
xfails until MET-568 lands compaction.
"""

from __future__ import annotations

from typing import Any

from chat_rubric_common import score, turn_replied

# Mirrors _HISTORY_LIMIT in api_gateway/chat/routes.py (baseline behavior).
HISTORY_LIMIT = 20

# Structural check ids this rubric can contribute (wiring-test contract).
CHECKS = (
    "stats_observed",
    "window_never_exceeded",
    "history_trim_reported",
    "late_turns_completed",
)


def _history_component(stats: dict[str, Any]) -> dict[str, Any] | None:
    for comp in stats.get("components") or []:
        if comp.get("key") == "history" or comp.get("label") == "history":
            return comp
    return None


def evaluate_chat_window(turns: list[dict[str, Any]]) -> dict[str, bool]:
    """Pure evaluator over the runner's turn records."""
    stats = [t["context_stats"] for t in turns if t.get("context_stats")]
    exceeded = [
        s for s in stats if (s.get("window") or 0) > 0 and (s.get("used") or 0) >= s["window"]
    ]
    trim_reported = True
    for s in stats:
        comp = _history_component(s)
        if comp is None:
            continue
        available = int(comp.get("items_available") or 0)
        included = int(comp.get("items_included") or 0)
        if available > HISTORY_LIMIT and included >= available:
            # More history exists than the slice can carry, but the telemetry
            # claims everything was included — the meter is lying.
            trim_reported = False
    late = turns[-5:]
    return {
        "stats_observed": bool(stats),
        "window_never_exceeded": bool(stats) and not exceeded,
        "history_trim_reported": trim_reported,
        "late_turns_completed": all(turn_replied(t) and not t.get("reply_error") for t in late),
    }


def score_chat_window(turns: list[dict[str, Any]]) -> dict[str, Any]:
    checks = evaluate_chat_window(turns)
    return {"score": score(checks), "checks": checks}
