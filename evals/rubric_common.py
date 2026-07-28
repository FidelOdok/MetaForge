#!/usr/bin/env python3
"""Shared rubric helpers (MET-10).

Goal-traceability catches the template-overfit failure mode: a "goal-driven"
handler that emits the same MPU-6050 defaults no matter the goal would score
well on presence-based checks while ignoring what was actually asked for. If the
goal names a specific part (a part number like BME280 / MPU-6050 / ESP32), the
produced artifacts should reference it.
"""

from __future__ import annotations

import re

# A part-number-ish token: >=2 letters then >=2 digits, optionally hyphenated
# (MPU-6050, BME280, ESP32, ATmega328). Excludes dimensions (40x30), rails
# (3.3V), and buses (I2C — only one digit).
_PART_RE = re.compile(r"\b[A-Za-z]{2,}-?\d{2,}[A-Za-z0-9]*\b")


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def goal_part_tokens(goal: str) -> set[str]:
    """Salient device part-numbers named in the goal, normalized (no hyphens)."""
    return {_norm(m.group(0)) for m in _PART_RE.finditer(goal or "")}


def goal_traceable(text: str, goal: str) -> bool:
    """True if the goal names no specific part, or the text references one.

    Vacuously true when the goal is generic (nothing specific to trace); a real
    signal only when the goal names a part number the artifacts should carry.
    """
    tokens = goal_part_tokens(goal)
    if not tokens:
        return True
    body = _norm(text)
    return any(tok in body for tok in tokens)
