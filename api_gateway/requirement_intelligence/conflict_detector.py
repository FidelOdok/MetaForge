"""RequirementConflictDetector (FORGE-257, gap G-A1 -- "conflict-free").

``linter.py``'s own docstring names ``contradictory_requirement`` as the one
lint category deliberately left out: real conflict detection needs
information from a whole REQUIREMENT SET, not a single string, and that's
the spec's own Conflict Agent (section 26.6) -- "not yet built by any
current Phase 3 sub-task" as of FORGE-55. This module is that piece, scoped
honestly to what a deterministic pass can actually prove:

Two requirements conflict when they impose PROVABLY INCOMPATIBLE numeric
bounds on the same measurable quantity -- an upper bound lower than another
requirement's lower bound, in the same unit (e.g. "shall weigh at most 2 kg"
+ "shall weigh at least 3 kg"). This is real, provable disagreement, not a
similarity heuristic -- the linter's own explicit standard for what NOT to
fake ("wrong far more often than a real linter should be").

What this honestly can't catch, and doesn't pretend to: conflicts with no
explicit bound+unit phrasing, conflicts about non-numeric claims, and
requirements that share a unit but describe a different physical quantity
(no unit-to-quantity mapping is attempted -- "2 kg" of payload isn't
distinguished from "2 kg" of enclosure mass; matching is unit-only, which is
the honest limit of what this module claims).
"""

from __future__ import annotations

import re
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel

_NUMBER = r"\d+(?:\.\d+)?"
_UNIT = r"[a-zA-Z%°]+"
_NUMBER_UNIT = re.compile(rf"({_NUMBER})\s*({_UNIT})")

# Longer/more specific phrases first within each list isn't required here --
# every cue is matched independently and all hits are kept.
_UPPER_CUES = (
    "at most",
    "no more than",
    "maximum of",
    "up to",
    "shall not exceed",
    "must not exceed",
    "less than or equal to",
)
_LOWER_CUES = (
    "at least",
    "no less than",
    "minimum of",
    "shall not be less than",
    "must not be less than",
    "greater than or equal to",
)

# Common written-out forms this module treats as equal to the SI/imperial
# token the linter's own `_UNIT_TOKENS` already recognizes -- not a full
# unit-conversion system, just spelling normalization so "2 kilograms" and
# "2 kg" are recognized as the same quantity.
_UNIT_ALIASES = {
    "kgs": "kg",
    "kilograms": "kg",
    "kilogram": "kg",
    "grams": "g",
    "gram": "g",
    "seconds": "s",
    "second": "s",
    "secs": "s",
    "sec": "s",
    "minutes": "min",
    "minute": "min",
    "mins": "min",
    "hours": "hr",
    "hour": "hr",
    "hrs": "hr",
    "decibels": "db",
    "decibel": "db",
    "celsius": "c",
    "degc": "c",
    "percent": "%",
    "watts": "w",
    "watt": "w",
    "volts": "v",
    "volt": "v",
    "amps": "a",
    "amp": "a",
    "millimeters": "mm",
    "millimetres": "mm",
    "meters": "m",
    "metres": "m",
    "newtons": "n",
    "newton": "n",
}

# Only look this many characters past a cue phrase for its number+unit --
# bounds the search to the cue's own clause rather than picking up an
# unrelated number later in a long requirement.
_LOOKAHEAD_CHARS = 25


class BoundType(StrEnum):
    UPPER = "upper"
    LOWER = "lower"


class _Bound(BaseModel):
    type: BoundType
    value: float
    unit: str


def _normalize_unit(raw: str) -> str:
    u = raw.strip().lower()
    return _UNIT_ALIASES.get(u, u)


def _extract_bounds(text: str) -> list[_Bound]:
    lower = text.lower()
    bounds: list[_Bound] = []
    for cues, bound_type in ((_UPPER_CUES, BoundType.UPPER), (_LOWER_CUES, BoundType.LOWER)):
        for cue in cues:
            start = 0
            while True:
                idx = lower.find(cue, start)
                if idx == -1:
                    break
                window = lower[idx + len(cue) : idx + len(cue) + _LOOKAHEAD_CHARS]
                match = _NUMBER_UNIT.search(window)
                if match:
                    bounds.append(
                        _Bound(
                            type=bound_type,
                            value=float(match.group(1)),
                            unit=_normalize_unit(match.group(2)),
                        )
                    )
                start = idx + len(cue)
    return bounds


def _incompatible(bounds_a: list[_Bound], bounds_b: list[_Bound]) -> str | None:
    for a in bounds_a:
        for b in bounds_b:
            if a.unit != b.unit:
                continue
            if a.type == BoundType.UPPER and b.type == BoundType.LOWER and b.value > a.value:
                return (
                    f"one requires at most {a.value:g}{a.unit}, "
                    f"the other at least {b.value:g}{a.unit}"
                )
            if a.type == BoundType.LOWER and b.type == BoundType.UPPER and a.value > b.value:
                return (
                    f"one requires at least {a.value:g}{a.unit}, "
                    f"the other at most {b.value:g}{a.unit}"
                )
    return None


class ConflictFinding(BaseModel):
    """A pair of requirements with provably incompatible numeric bounds."""

    requirement_a_id: str
    requirement_b_id: str
    detail: str


class RequirementConflictDetector:
    """Deterministic, unit-matched numeric-bound conflict detection across a
    requirement set (see module docstring for exactly what this can and
    can't catch)."""

    def detect(self, requirements: list[tuple[UUID, str]]) -> list[ConflictFinding]:
        parsed = [(rid, _extract_bounds(text)) for rid, text in requirements]
        findings: list[ConflictFinding] = []
        for i, (id_a, bounds_a) in enumerate(parsed):
            if not bounds_a:
                continue
            for id_b, bounds_b in parsed[i + 1 :]:
                if id_a == id_b or not bounds_b:
                    continue
                detail = _incompatible(bounds_a, bounds_b)
                if detail:
                    findings.append(
                        ConflictFinding(
                            requirement_a_id=str(id_a),
                            requirement_b_id=str(id_b),
                            detail=detail,
                        )
                    )
        return findings
