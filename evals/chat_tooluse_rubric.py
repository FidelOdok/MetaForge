#!/usr/bin/env python3
"""Tool-call trajectory rubric for the chat context evals (MET-570).

The harness captures full per-turn trajectories (``agent.step``: tool,
arguments, observation, error) but nothing scored them until now. These checks
target the tool-discipline rules the system prompt states (don't repeat an
identical call, don't retry a call that just failed with the same arguments)
and trajectory health (error rate bounded, turns with huge tool observations
still reach a real answer — the native path caps observations at 8KB while the
ReAct trace does not, an asymmetry the ``bigobs`` scenarios expose).
"""

from __future__ import annotations

from typing import Any

from chat_rubric_common import canon_args, score, tool_steps, turn_replied

# Structural check ids this rubric can contribute (wiring-test contract).
CHECKS = (
    "no_duplicate_identical_calls",
    "no_repeated_failing_call",
    "tool_errors_bounded",
    "big_observation_survived",
)

# More than a third of tool calls erroring means the model is flailing.
_MAX_ERROR_RATE = 1 / 3


def evaluate_chat_tooluse(turns: list[dict[str, Any]]) -> dict[str, bool]:
    """Pure evaluator over the runner's turn records."""
    dup_free = True
    refail_free = True
    total = 0
    errored = 0
    for t in turns:
        seen: set[tuple[Any, str]] = set()
        failed: set[tuple[Any, str]] = set()
        for s in tool_steps(t):
            total += 1
            key = (s.get("tool"), canon_args(s.get("arguments")))
            if key in seen:
                dup_free = False
            if key in failed:
                refail_free = False
            seen.add(key)
            if s.get("error"):
                errored += 1
                failed.add(key)
    bigobs = [t for t in turns if t.get("tag") == "bigobs"]
    return {
        "no_duplicate_identical_calls": dup_free,
        "no_repeated_failing_call": refail_free,
        "tool_errors_bounded": total == 0 or errored / total < _MAX_ERROR_RATE,
        "big_observation_survived": all(turn_replied(t) for t in bigobs) if bigobs else True,
    }


def score_chat_tooluse(turns: list[dict[str, Any]]) -> dict[str, Any]:
    checks = evaluate_chat_tooluse(turns)
    return {"score": score(checks), "checks": checks}
