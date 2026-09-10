#!/usr/bin/env python3
"""Memory-retention rubric for the chat context evals (MET-570).

The recall checks themselves are declared in the scenario JSON (seed a fact in
an early turn, probe it later — the Phase A / MET-565 amnesia bug class and the
20-turn-slice loss MET-568 will fix). This rubric adds the structural half:
the probe turns must have produced *real* answers at all — an empty reply or
the step-budget bailout would make a recall "failure" meaningless.
"""

from __future__ import annotations

from typing import Any

from chat_rubric_common import score, turn_replied

# Structural check ids this rubric can contribute (wiring-test contract).
CHECKS = ("probe_turns_replied", "no_error_turns")


def evaluate_chat_memory(turns: list[dict[str, Any]]) -> dict[str, bool]:
    """Pure evaluator over the runner's turn records."""
    probes = [t for t in turns if t.get("checks")]
    return {
        "probe_turns_replied": bool(probes) and all(turn_replied(t) for t in probes),
        "no_error_turns": all(not t.get("reply_error") for t in turns),
    }


def score_chat_memory(turns: list[dict[str, Any]]) -> dict[str, Any]:
    checks = evaluate_chat_memory(turns)
    return {"score": score(checks), "checks": checks}
