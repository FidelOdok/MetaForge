"""Compare analysis constraints to the latest simulation_result (FORGE-498).

``geometry_constraints`` covers what a cad_model records about itself. A
deflection, stress or safety-factor limit needs analysis evidence, so until now
the gate reported them as not evaluated and a ``simulation_result`` whose
verdict read ``pass`` while it broke the stated deflection limit went through.

This module reads the numbers a ``simulation_result`` records and compares
them to the limits the requirements state. Every constraint ends in exactly one
of three states: passed, violated (with the numbers), or not evaluated (with
the reason). A constraint that cannot be compared is never passed.

Load-case scaling. A requirement is usually a *service-load* limit while the
analysis is run at a *factored* load. When the constraint names its load basis
(``service`` or ``factored``/``ultimate``) and the result records both loads,
the result is scaled linearly to the constraint's load: displacement and stress
by ``target / analysed`` load, safety factor by ``analysed / target``. The
finding states the scaling. When the result records two different loads and the
constraint names neither, the basis is unknown and the constraint is not
evaluated.

Which result: the latest ``simulation_result`` linked to each current
``cad_model`` (``metadata.source_cad_model_id`` or a ``derives_from`` edge). A
current model with no linked result is reported as not evaluated; a result for
a superseded revision is never used.

Stale evidence (FORGE-527). A result is also linked to a current model through
its revision pins (``metadata.depends_on_items``, the model's item key), so a
result recorded on an older revision of the same part is found even though it
names the old node. A result whose ``staleness`` is not ``current`` /
``revalidated`` never satisfies a constraint: when it is the only result for a
model, every analysis constraint on that model gets a finding naming the record
and the revision it was for (a violation at error severity), so the gate cannot
pass on it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from api_gateway.runs.geometry_constraints import _OPS, _TO_MM
from twin_core.consistency.record_pins import describe_staleness, is_valid_evidence, record_pins

_STRESS_TO_MPA = {
    "": 1.0,
    "mpa": 1.0,
    "n/mm2": 1.0,
    "pa": 1e-6,
    "kpa": 1e-3,
    "gpa": 1e3,
    "psi": 0.006894757,
    "ksi": 6.894757,
}

_VALUE_KEYS: dict[str, tuple[str, ...]] = {
    "deflection": (
        "max_displacement_mm",
        "max_deflection_mm",
        "displacement_mm",
        "deflection_mm",
    ),
    "stress": ("max_von_mises_mpa", "max_stress_mpa", "von_mises_mpa", "max_von_mises"),
    "safety_factor": ("safety_factor", "min_safety_factor"),
}
_SF_KEY = re.compile(r"(^|_)(sf|safety_factor)(_|$)")
_LOAD_KEYS = ("applied_load_n", "analysis_load_n", "analysed_load_n", "load_n", "factored_load_n")
_SF_WORD = re.compile(r"(^|[^a-z])sf([^a-z]|$)")


@dataclass
class SimResult:
    """A ``simulation_result`` work product, with the cad_models it derives from."""

    id: str
    name: str
    updated_at: float
    metadata: dict[str, Any]
    cad_ids: set[str] = field(default_factory=set)

    @property
    def item_keys(self) -> set[str]:
        """The item keys this result is pinned to (FORGE-527)."""
        return {str(p["item_key"]) for p in record_pins(self.metadata)}

    @property
    def valid(self) -> bool:
        return is_valid_evidence(self.metadata)

    @property
    def stale_finding(self) -> str | None:
        return describe_staleness(self.name, self.metadata)


@dataclass
class AnalysisCheck:
    """Outcome of comparing analysis constraints to simulation results."""

    violations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    satisfied: list[str] = field(default_factory=list)
    not_evaluated: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    evaluated: int = 0


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


_FORCE_UNITS = frozenset({"n", "kn", "lbf"})
_LENGTH_UNITS = frozenset({"mm", "cm", "m", "in", "inch"})
_PRESSURE_UNITS = frozenset({"mpa", "pa", "kpa", "gpa", "psi", "ksi", "n/mm2"})
_DIMENSIONLESS_UNITS = frozenset({"-", "1", "x", "ratio", "factor", "dimensionless"})
_UNIT_KIND = {
    **dict.fromkeys(_FORCE_UNITS, "load"),
    **dict.fromkeys(_LENGTH_UNITS, "deflection"),
    **dict.fromkeys(_PRESSURE_UNITS, "stress"),
    **dict.fromkeys(_DIMENSIONLESS_UNITS, "safety_factor"),
}


def _name_kind(constraint: Any) -> str | None:
    text = f"{getattr(constraint, 'metric', '') or ''} {getattr(constraint, 'name', '') or ''}"
    text = text.lower()
    if "safety" in text or _SF_WORD.search(text):
        return "safety_factor"
    if "deflect" in text or "displacement" in text:
        return "deflection"
    if "stress" in text or "von_mises" in text or "von mises" in text:
        return "stress"
    return None


def _normalise_unit(constraint: Any) -> str:
    unit = str(getattr(constraint, "unit", "") or "").strip().lower()
    return unit.replace("\u00b2", "2").replace(" ", "")


def classify_checked(constraint: Any) -> tuple[str | None, str | None]:
    """``(kind, reason)``: the unit decides, the name only when the unit is absent.

    ``kind`` is ``deflection`` | ``stress`` | ``safety_factor`` | ``None``.
    ``None`` with a ``reason`` means the unit contradicts the name (not
    evaluated); ``None`` without one means the constraint is not an analysis
    result check (a force limit is a requirement input, not a result).
    """
    unit = _normalise_unit(constraint)
    named = _name_kind(constraint)
    if not unit:
        return named, None
    unit_kind = _UNIT_KIND.get(unit)
    if unit_kind is None:
        return named, None  # unrecognised unit: the caller reports it as unsupported
    if unit_kind == "load":
        if named in ("deflection", "stress"):
            return None, f"unit '{unit}' is a force but the name reads as {named}"
        return None, None
    if unit_kind == "deflection" and named is None:
        return None, None  # a bare length is a geometry limit (envelope, thickness)
    if named is not None and named != unit_kind:
        return (
            None,
            f"unit '{unit}' is a {unit_kind.replace('_', ' ')} unit "
            f"but the name reads as {named.replace('_', ' ')}",
        )
    return unit_kind, None


def classify(constraint: Any) -> str | None:
    """``deflection`` | ``stress`` | ``safety_factor`` | ``None`` (not an analysis metric)."""
    return classify_checked(constraint)[0]


def _load_basis(constraint: Any) -> str | None:
    meta = getattr(constraint, "metadata", None) or {}
    text = " ".join(
        str(x or "")
        for x in (
            getattr(constraint, "metric", ""),
            getattr(constraint, "name", ""),
            getattr(constraint, "acceptance_criteria", ""),
            getattr(constraint, "message", ""),
            meta.get("load_basis"),
            meta.get("load_case"),
        )
    ).lower()
    service = "service" in text
    factored = any(w in text for w in ("factored", "ultimate", "design load"))
    if service == factored:  # neither named, or contradictory
        return None
    return "service" if service else "factored"


def _result_value(kind: str, meta: dict[str, Any]) -> tuple[float, str] | None:
    for key in _VALUE_KEYS[kind]:
        value = _num(meta.get(key))
        if value is not None:
            return value, key
    if kind == "safety_factor":
        found = [
            (v, k)
            for k, raw in meta.items()
            if _SF_KEY.search(str(k).lower()) and (v := _num(raw)) is not None
        ]
        if found:
            return min(found)  # several materials: the weakest governs
    return None


def _scaling(constraint: Any, meta: dict[str, Any]) -> tuple[float, str, str | None]:
    """``(load_ratio, note, reason)``: ratio of target load to analysed load.

    ``reason`` is set when the load basis cannot be resolved (not evaluated).
    """
    service = _num(meta.get("service_load_n"))
    factored = _num(meta.get("factored_load_n"))
    analysed = next((v for k in _LOAD_KEYS if (v := _num(meta.get(k))) is not None), None)
    if analysed is None:
        analysed = service
    basis = _load_basis(constraint)
    loads = {v for v in (service, factored, analysed) if v is not None}
    if basis is None:
        if len(loads) > 1:
            return (
                1.0,
                "",
                (
                    "load basis unknown: the result records both a service and a factored "
                    "load and the constraint names neither"
                ),
            )
        return 1.0, "compared as recorded, no load scaling applied", None
    target = service if basis == "service" else factored
    if target is None or analysed is None or analysed <= 0:
        return (
            1.0,
            "",
            (
                f"constraint is a {basis}-load limit but the result does not record the "
                f"{basis} load and the analysed load, so it cannot be scaled"
            ),
        )
    ratio = target / analysed
    if abs(ratio - 1.0) < 1e-9:
        return 1.0, f"analysed at the {basis} load {target:g} N", None
    return (
        ratio,
        (f"scaled linearly from {analysed:g} N analysed to the {basis} load {target:g} N"),
        None,
    )


def _assumptions(meta: dict[str, Any]) -> str:
    raw = meta.get("modelling_assumptions")
    if isinstance(raw, list | tuple):
        return "; ".join(str(x) for x in raw if str(x).strip())
    return str(raw).strip() if raw else ""


def check_analysis_constraints(
    constraints: Iterable[Any],
    cad_models: Iterable[tuple[str, str]],
    results: Iterable[SimResult],
    model_keys: dict[str, str] | None = None,
) -> AnalysisCheck:
    """Evaluate analysis constraints against ``(cad_id, name)`` models and results.

    ``model_keys`` maps a cad_id to its item key (FORGE-527), so a result pinned
    to an older revision of the same item is found and reported as stale.
    """
    out = AnalysisCheck()
    models = list(cad_models)
    sims = list(results)
    keys = model_keys or {}
    # Latest valid result per current cad_model; a stale one only when no
    # valid result exists, so the gate can name it instead of passing on it.
    chosen: list[tuple[str, SimResult]] = []
    for cad_id, cad_name in models:
        key = keys.get(cad_id)
        linked = [s for s in sims if cad_id in s.cad_ids or (key and key in s.item_keys)]
        if not linked:
            continue
        valid = [s for s in linked if s.valid]
        chosen.append((cad_name, max(valid or linked, key=lambda s: s.updated_at)))
    seen_assumptions: set[str] = set()

    for constraint in constraints:
        kind, mismatch = classify_checked(constraint)
        limit = _num(getattr(constraint, "limit", None))
        severity = str(getattr(getattr(constraint, "severity", ""), "value", "") or "error")
        if (kind is None and mismatch is None) or limit is None or severity == "info":
            continue
        label = str(getattr(constraint, "name", "") or getattr(constraint, "metric", "constraint"))
        if kind is None:
            out.not_evaluated.append(f"{label}: {mismatch}")
            continue
        op_name = str(getattr(constraint, "operator", "") or "<=")
        compare = _OPS.get(op_name)
        unit = str(getattr(constraint, "unit", "") or "").lower()
        factor = (_STRESS_TO_MPA if kind == "stress" else _TO_MM).get(unit)
        if kind == "safety_factor":
            factor = 1.0
        if compare is None or factor is None:
            out.not_evaluated.append(f"{label}: unsupported operator or unit")
            continue
        if not chosen:
            out.not_evaluated.append(
                f"{label}: no simulation_result is linked to the current cad_model"
                if models
                else f"{label}: no cad_model committed to analyse"
            )
            continue
        sink = out.violations if severity == "error" else out.warnings
        target = limit * factor
        shown_unit = {"deflection": "mm", "stress": "MPa", "safety_factor": ""}[kind]
        for cad_name, sim in chosen:
            where = f"'{sim.name}' (analysis of '{cad_name}')"
            stale = sim.stale_finding
            if stale:
                sink.append(f"{label}: {stale}")
                continue
            found = _result_value(kind, sim.metadata)
            if found is None:
                out.not_evaluated.append(f"{label}: {where} records no {kind.replace('_', ' ')}")
                continue
            raw, _key = found
            ratio, note, reason = _scaling(constraint, sim.metadata)
            if reason:
                out.not_evaluated.append(f"{label}: {where}: {reason}")
                continue
            value = raw / ratio if kind == "safety_factor" else raw * ratio
            out.evaluated += 1
            text = f"{value:.2f}".rstrip("0").rstrip(".")
            req = f"{op_name} {target:g}"
            unit_sfx = f" {shown_unit}" if shown_unit else ""
            detail = f"{label}: {where} {text}{unit_sfx}"
            if abs(value - raw) > 1e-9:
                detail += f" (recorded {raw:g}"
                detail += f"; {note})" if note else ")"
            elif note:
                detail += f" ({note})"
            if compare(value, target):
                out.satisfied.append(f"{detail}, requirement {req}")
            else:
                sink.append(
                    f"{detail}, requirement is {req}{' ' + shown_unit if shown_unit else ''}"
                )
            assumed = _assumptions(sim.metadata)
            if assumed and sim.id not in seen_assumptions:
                seen_assumptions.add(sim.id)
                out.assumptions.append(f"{sim.name}: {assumed}")
    # Surface assumptions of every chosen result, even with no analysis constraint.
    for _cad_name, sim in chosen:
        if not sim.valid:
            continue
        assumed = _assumptions(sim.metadata)
        if assumed and sim.id not in seen_assumptions:
            seen_assumptions.add(sim.id)
            out.assumptions.append(f"{sim.name}: {assumed}")
    return out
