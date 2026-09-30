"""How a chosen component stands against the project's requirements (FORGE-346).

D3 is "component selection with requirement vs spec margins". Selection and
BOM commit already worked: ``component.search_*`` finds candidates and
``twin.record_component_selection`` persists the chosen one as a BOMItem.
What nothing did was compare the part's specs to the requirements the
project had already recorded -- so a part that violates one could be
committed, and the violation surfaced later, at a gate, with no hint that
the decision to buy it was where it went wrong.

This is only checkable because FORGE-259/344 made requirements typed: a
``Constraint`` with ``metric``, ``operator``, ``limit`` and ``unit`` is
something a spec value can be held against. An untyped requirement is
reported as unchecked rather than quietly skipped -- the distinction this
codebase keeps everywhere, because "no margin shown" and "no requirement"
look identical otherwise.

Never claims a comparison it cannot substantiate. A spec with no unit is
compared against the requirement's unit and flagged as an assumption; a
spec in an incompatible unit is not compared at all.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel

from twin_core.models.quantity import Quantity, is_valid_unit

__all__ = [
    "SpecMargin",
    "UncheckedRequirement",
    "compare_specs_to_requirements",
]

#: Reasons a requirement could not be held against the specs. Stable
#: strings so a caller can branch without parsing prose.
NO_MATCHING_SPEC = "no_matching_spec"
SPEC_NOT_NUMERIC = "spec_not_numeric"
INCOMPATIBLE_UNITS = "incompatible_units"
REQUIREMENT_NOT_BOUND = "requirement_not_bound"

_NUMBER_WITH_UNIT = re.compile(r"^\s*(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*([A-Za-zΩµ/·^%]*)\s*$")

_COMPARATORS = {
    "<=": lambda actual, limit: actual <= limit,
    "<": lambda actual, limit: actual < limit,
    ">=": lambda actual, limit: actual >= limit,
    ">": lambda actual, limit: actual > limit,
    "==": lambda actual, limit: actual == limit,
    "!=": lambda actual, limit: actual != limit,
}


class SpecMargin(BaseModel):
    """One requirement, held against one of the part's specs."""

    requirement: str
    metric: str
    operator: str
    limit: float
    unit: str
    spec_value: float
    """In the requirement's unit, converted when the spec carried its own."""
    satisfied: bool
    margin: float | None
    """Signed headroom in the requirement's unit: positive means slack.

    None for ``==``/``!=``, where "how far over" is not a meaningful
    number and reporting one would invite a comparison nobody should make.
    """
    margin_pct: float | None
    unit_assumed: bool = False
    """The spec was a bare number, read as the requirement's unit.

    A real risk, not a formality: a 3300 that is microfarads read as
    farads satisfies almost anything.
    """


class UncheckedRequirement(BaseModel):
    """A requirement no margin could be computed for, and why."""

    requirement: str
    metric: str
    reason: str
    detail: str = ""


def _as_quantity(value: Any) -> tuple[float, str] | None:
    """Pull ``(magnitude, unit)`` out of whatever a catalog row carries.

    Catalog specs are free-form: a number, a numeric string, "36 V", or a
    ``{"value": ..., "unit": ...}`` pair. An empty unit means the value
    was bare.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value), ""
    if isinstance(value, dict):
        raw = value.get("value")
        unit = value.get("unit")
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            return float(raw), str(unit or "")
        return None
    if isinstance(value, str):
        match = _NUMBER_WITH_UNIT.match(value)
        if match is None:
            return None
        return float(match.group(1)), match.group(2)
    return None


def compare_specs_to_requirements(
    specs: dict[str, Any],
    constraints: list[Any],
) -> tuple[list[SpecMargin], list[UncheckedRequirement]]:
    """Hold ``specs`` against every typed requirement in ``constraints``.

    ``constraints`` are ``Constraint``-shaped: anything with ``name``,
    ``metric``, ``operator``, ``limit`` and ``unit`` attributes. Returns
    the margins computed and, separately, the requirements that could not
    be checked -- an empty margin list with an empty unchecked list means
    the project has no typed requirements at all, which is a different
    thing from a part that passes everything.
    """
    margins: list[SpecMargin] = []
    unchecked: list[UncheckedRequirement] = []

    for constraint in constraints:
        name = str(getattr(constraint, "name", "") or "?")
        metric = str(getattr(constraint, "metric", "") or "")
        limit = getattr(constraint, "limit", None)
        if not metric or limit is None:
            unchecked.append(
                UncheckedRequirement(
                    requirement=name,
                    metric=metric,
                    reason=REQUIREMENT_NOT_BOUND,
                    detail="no metric/limit pair to compare a spec against",
                )
            )
            continue

        if metric not in specs:
            unchecked.append(
                UncheckedRequirement(requirement=name, metric=metric, reason=NO_MATCHING_SPEC)
            )
            continue

        parsed = _as_quantity(specs[metric])
        if parsed is None:
            unchecked.append(
                UncheckedRequirement(
                    requirement=name,
                    metric=metric,
                    reason=SPEC_NOT_NUMERIC,
                    detail=f"spec value {specs[metric]!r} is not a number",
                )
            )
            continue

        value, spec_unit = parsed
        req_unit = str(getattr(constraint, "unit", "") or "")
        unit_assumed = False
        if spec_unit and req_unit and spec_unit != req_unit:
            if not (is_valid_unit(spec_unit) and is_valid_unit(req_unit)):
                unchecked.append(
                    UncheckedRequirement(
                        requirement=name,
                        metric=metric,
                        reason=INCOMPATIBLE_UNITS,
                        detail=f"cannot convert {spec_unit!r} to {req_unit!r}",
                    )
                )
                continue
            try:
                value = Quantity(value=value, unit=spec_unit).to(req_unit).value
            except Exception:  # noqa: BLE001 — an unconvertible pair is an answer
                unchecked.append(
                    UncheckedRequirement(
                        requirement=name,
                        metric=metric,
                        reason=INCOMPATIBLE_UNITS,
                        detail=f"cannot convert {spec_unit!r} to {req_unit!r}",
                    )
                )
                continue
        elif not spec_unit and req_unit:
            # Bare number. Comparing it is still the useful thing to do --
            # refusing would leave most catalog rows unchecked -- but the
            # caller has to be told the unit was assumed.
            unit_assumed = True

        operator = str(getattr(constraint, "operator", "") or "<=")
        compare = _COMPARATORS.get(operator, _COMPARATORS["<="])
        limit_f = float(limit)
        satisfied = bool(compare(value, limit_f))

        # Signed slack, in the direction the requirement cares about. An
        # equality has no "how far over", and inventing one would invite a
        # comparison between parts that does not mean anything.
        if operator in ("<=", "<"):
            margin: float | None = limit_f - value
        elif operator in (">=", ">"):
            margin = value - limit_f
        else:
            margin = None
        margin_pct = (
            (margin / abs(limit_f) * 100.0) if margin is not None and limit_f != 0 else None
        )

        margins.append(
            SpecMargin(
                requirement=name,
                metric=metric,
                operator=operator,
                limit=limit_f,
                unit=req_unit,
                spec_value=value,
                satisfied=satisfied,
                margin=margin,
                margin_pct=margin_pct,
                unit_assumed=unit_assumed,
            )
        )

    return margins, unchecked
