"""Compare a project's structured constraints to its committed cad_model (FORGE-496).

The constraint engine evaluates each ``Constraint.expression``. A requirement
recorded as a structured ``metric`` + ``limit`` binding carries the placeholder
expression ``True`` (see ``constraint_recorder``), so it can never fail and a
gate reported "164 evaluated, 0 violations" for a model that broke the stated
stock thickness and material.

This module is the minimum honest check that closes that gap. It reads only
what a cad_model records about itself and compares it to the limits the
requirements state.

Covered:

- **Envelope / size** (metric names containing ``envelope``, ``length``,
  ``width``, ``height``, ``depth``, ``size``, ``dimension``): the model's
  bounding-box extents, sorted descending (``length`` >= ``width`` >=
  ``height``), against the limit.
- **Thickness / stock** (``thickness``, ``stock``): the smallest bounding-box
  extent of a single-part model. A multi-part assembly is checked part by part
  when its parts record dimensions, and skipped otherwise.
- **Material** (``material``): the cad_model's recorded material must share a
  material family word with the one the requirement names.

Not covered (reported as not evaluated, never as passing): per-part limits on
an assembly whose parts record no dimensions, orientation-specific axes, any
metric outside the list above (mass, deflection, safety factor: those need
analysis evidence, not geometry), and metrics naming ``print`` (a printed-part
limit applies to each part, not to the assembled bounding box).
"""

from __future__ import annotations

import operator as _op
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from twin_core.models.quantity import is_currency_code, same_currency

_OPS: dict[str, Callable[[float, float], bool]] = {
    "<=": _op.le,
    "<": _op.lt,
    ">=": _op.ge,
    ">": _op.gt,
    "==": lambda a, b: abs(a - b) < 1e-6,
    "!=": lambda a, b: abs(a - b) >= 1e-6,
}

_TO_MM = {"": 1.0, "mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4, "inch": 25.4}

_SIZE_WORDS = ("envelope", "length", "width", "height", "depth", "size", "dimension")
_THICKNESS_WORDS = ("thickness", "stock")

# Words that do not identify a material family.
_GENERIC_MATERIAL_WORDS = frozenset(
    {"mm", "the", "and", "with", "wood", "board", "sheet", "offcut", "stock", "material", "grade"}
)


@dataclass
class GeometryCheck:
    """Outcome of comparing constraints to a project's cad_models."""

    violations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    evaluated: int = 0
    not_evaluated: list[str] = field(default_factory=list)


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _extents_from_bbox(bbox: Any) -> tuple[float, float, float] | None:
    if not isinstance(bbox, dict):
        return None
    if all(k in bbox for k in ("min_x", "max_x", "min_y", "max_y", "min_z", "max_z")):
        vals = [
            _num(bbox["max_x"]) - _num(bbox["min_x"]),  # type: ignore[operator]
            _num(bbox["max_y"]) - _num(bbox["min_y"]),  # type: ignore[operator]
            _num(bbox["max_z"]) - _num(bbox["min_z"]),  # type: ignore[operator]
        ]
        return (abs(vals[0]), abs(vals[1]), abs(vals[2]))
    lo, hi = bbox.get("min"), bbox.get("max")
    if isinstance(lo, list | tuple) and isinstance(hi, list | tuple) and len(lo) == len(hi) == 3:
        nums = [(_num(h), _num(low)) for h, low in zip(hi, lo, strict=True)]
        if all(a is not None and b is not None for a, b in nums):
            return tuple(abs(a - b) for a, b in nums)  # type: ignore[operator,return-value]
    return None


def _extents_from_dimensions(dims: Any) -> tuple[float, float, float] | None:
    if isinstance(dims, dict):
        vals = [_num(v) for v in dims.values()]
    elif isinstance(dims, list | tuple):
        vals = [_num(v) for v in dims]
    else:
        return None
    if len(vals) != 3 or any(v is None for v in vals):
        return None
    return tuple(abs(v) for v in vals)  # type: ignore[arg-type,return-value]


def extents_mm(metadata: dict[str, Any]) -> tuple[float, float, float] | None:
    """Bounding extents (mm) sorted descending, or ``None`` when none are recorded."""
    features = metadata.get("geometry_features") or {}
    candidates = (
        _extents_from_dimensions(metadata.get("dimensions_mm")),
        _extents_from_bbox(metadata.get("bbox_mm")),
        _extents_from_bbox((features.get("properties") or {}).get("bounding_box")),
    )
    for found in candidates:
        if found is not None:
            a, b, c = sorted(found, reverse=True)
            return (a, b, c)
    return None


def _part_extents(metadata: dict[str, Any]) -> list[tuple[str, tuple[float, float, float]]] | None:
    """Per-part extents of an assembly, or ``None`` when parts record no dimensions."""
    assembly = metadata.get("assembly")
    parts = assembly.get("parts") if isinstance(assembly, dict) else None
    if not isinstance(parts, list) or not parts:
        return None
    out: list[tuple[str, tuple[float, float, float]]] = []
    for part in parts:
        if not isinstance(part, dict):
            return None
        ext = extents_mm(part)
        if ext is None:
            return None
        out.append((str(part.get("name") or part.get("label") or "part"), ext))
    return out


def is_assembly(metadata: dict[str, Any]) -> bool:
    assembly = metadata.get("assembly")
    parts = assembly.get("parts") if isinstance(assembly, dict) else None
    return isinstance(parts, list) and len(parts) > 1


def _material_words(text: str) -> set[str]:
    return {
        w
        for w in re.findall(r"[a-z][a-z0-9]+", text.lower())
        if w not in _GENERIC_MATERIAL_WORDS and len(w) > 2
    }


def _material_matches(required: str, recorded: str) -> bool:
    req = _material_words(required)
    if not req:
        return True  # nothing identifying to compare
    have = _material_words(recorded)
    # "plywood" / "ply" are the same family.
    have |= {"plywood"} if "ply" in have else set()
    req |= {"plywood"} if "ply" in req else set()
    return bool(req & have)


def classify(metric: str) -> str | None:
    """``envelope`` | ``thickness`` | ``material`` | ``None`` (not a geometry metric)."""
    m = metric.lower()
    if not m or "print" in m:
        return None
    if "material" in m:
        return "material"
    if any(w in m for w in _THICKNESS_WORDS):
        return "thickness"
    if any(w in m for w in _SIZE_WORDS):
        return "envelope"
    return None


def _axis_index(metric: str) -> int:
    m = metric.lower()
    if "width" in m:
        return 1
    if "height" in m or "depth" in m:
        return 2
    return 0  # length, or an unnamed envelope: the largest extent


def _label(constraint: Any) -> str:
    return str(getattr(constraint, "name", "") or getattr(constraint, "metric", "constraint"))


def _required_material(constraint: Any) -> str:
    meta = getattr(constraint, "metadata", None) or {}
    for key in ("material", "required_material"):
        if meta.get(key):
            return str(meta[key])
    return str(
        getattr(constraint, "acceptance_criteria", "") or getattr(constraint, "message", "") or ""
    )


def _check_cost(
    constraint: Any, models: list[tuple[str, dict[str, Any]]], result: GeometryCheck
) -> None:
    """FORGE-515: a currency limit is compared only with a cost in the same currency.

    A model recording no cost, or a cost in another currency, is reported as
    not evaluated with the reason (no exchange rate is ever assumed).
    """
    limit = getattr(constraint, "limit", None)
    severity = str(getattr(getattr(constraint, "severity", ""), "value", "") or "error")
    compare = _OPS.get(str(getattr(constraint, "operator", "") or "<="))
    if limit is None or compare is None or severity == "info":
        return
    sink = result.violations if severity == "error" else result.warnings
    label = _label(constraint)
    unit = str(constraint.unit).strip()
    for model_name, meta in models:
        cost = _num(meta.get("cost"))
        currency = str(meta.get("cost_currency") or "")
        if cost is None:
            result.not_evaluated.append(f"{label}: '{model_name}' records no cost")
        elif not same_currency(unit, currency):
            result.not_evaluated.append(
                f"{label}: limit is in {unit} but '{model_name}' cost is in "
                f"{currency or 'an unknown currency'} (no exchange rate)"
            )
        else:
            result.evaluated += 1
            if not compare(cost, float(limit)):
                sink.append(
                    f"{label}: cad_model '{model_name}' cost {cost:g} {unit} "
                    f"breaks {constraint.operator} {float(limit):g} {unit}"
                )


def check_geometry_constraints(
    constraints: Iterable[Any], cad_models: Iterable[tuple[str, dict[str, Any]]]
) -> GeometryCheck:
    """Evaluate structured geometry constraints against ``(name, metadata)`` cad_models."""
    result = GeometryCheck()
    models = list(cad_models)
    for constraint in constraints:
        unit = str(getattr(constraint, "unit", "") or "")
        if is_currency_code(unit):
            _check_cost(constraint, models, result)
            continue
        kind = classify(str(getattr(constraint, "metric", "") or ""))
        limit = getattr(constraint, "limit", None)
        severity = str(getattr(getattr(constraint, "severity", ""), "value", "") or "error")
        if kind is None or (kind != "material" and limit is None) or severity == "info":
            continue
        sink = result.violations if severity == "error" else result.warnings
        label = _label(constraint)
        if not models:
            result.not_evaluated.append(f"{label}: no cad_model committed")
            continue
        op_name = str(getattr(constraint, "operator", "") or "<=")
        compare = _OPS.get(op_name)
        scale = _TO_MM.get(str(getattr(constraint, "unit", "") or "").lower())
        for model_name, meta in models:
            if kind == "material":
                required = _required_material(constraint)
                recorded = str(meta.get("material") or "")
                result.evaluated += 1
                if not recorded:
                    sink.append(f"{label}: cad_model '{model_name}' records no material")
                elif not _material_matches(required, recorded):
                    sink.append(
                        f"{label}: cad_model '{model_name}' material '{recorded}' "
                        f"does not match required '{required}'"
                    )
                continue
            if compare is None or scale is None:
                result.not_evaluated.append(f"{label}: unsupported operator or unit")
                continue
            target = float(limit) * scale
            if kind == "thickness":
                if is_assembly(meta):
                    parts = _part_extents(meta)
                    if parts is None:
                        result.not_evaluated.append(
                            f"{label}: '{model_name}' is an assembly whose parts record no size"
                        )
                        continue
                    values = [(f"{model_name}/{n}", e[2]) for n, e in parts]
                else:
                    ext = extents_mm(meta)
                    if ext is None:
                        result.not_evaluated.append(f"{label}: '{model_name}' records no size")
                        continue
                    values = [(model_name, ext[2])]
            else:
                ext = extents_mm(meta)
                if ext is None:
                    result.not_evaluated.append(f"{label}: '{model_name}' records no size")
                    continue
                values = [(model_name, ext[_axis_index(str(constraint.metric))])]
            for where, value in values:
                result.evaluated += 1
                if not compare(value, target):
                    sink.append(
                        f"{label}: {where} measures {value:g} mm, "
                        f"requirement is {op_name} {target:g} mm"
                    )
    return result


# FORGE-511: multi-part designs must commit an assembly.

#: Boxes that overlap by no more than this (mm) are touching, not interfering.
TOUCH_TOLERANCE_MM = 0.01


def _bounds(bbox: Any) -> tuple[tuple[float, float, float], tuple[float, float, float]] | None:
    """``(min, max)`` corners of a bbox in either recorded shape, or ``None``."""
    if not isinstance(bbox, dict):
        return None
    keys = ("min_x", "min_y", "min_z", "max_x", "max_y", "max_z")
    if all(k in bbox for k in keys):
        v = [_num(bbox[k]) for k in keys]
        if any(x is None for x in v):
            return None
        return (v[0], v[1], v[2]), (v[3], v[4], v[5])  # type: ignore[return-value]
    lo, hi = bbox.get("min"), bbox.get("max")
    if isinstance(lo, list | tuple) and isinstance(hi, list | tuple) and len(lo) == len(hi) == 3:
        low = [_num(x) for x in lo]
        high = [_num(x) for x in hi]
        if any(x is None for x in low + high):
            return None
        return tuple(low), tuple(high)  # type: ignore[return-value]
    return None


def assembly_parts(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    """The part entries of an assembly cad_model (empty when not one).

    Read from ``metadata.parts`` (the FORGE-511 ``parts`` argument of
    ``twin.commit_geometry``) or, for assemblies committed with the part list
    in ``parameters``, from ``metadata.geometry_features.parameters.parts``.
    """
    parts = metadata.get("parts")
    if not isinstance(parts, list):
        features = metadata.get("geometry_features")
        params = features.get("parameters") if isinstance(features, dict) else None
        parts = params.get("parts") if isinstance(params, dict) else None
    if not isinstance(parts, list):
        return []
    return [p for p in parts if isinstance(p, dict)]


def check_assembly(models: Iterable[tuple[str, str, dict[str, Any]]]) -> GeometryCheck:
    """FORGE-511: a design with several parts needs an assembly that places them sanely.

    ``models`` are ``(node_id, name, metadata)`` for the project's current
    cad_models. A model is an assembly when ``metadata.parts`` lists parts.
    Violations: more than one part but no assembly referencing them all;
    overlapping part position boxes; an assembly box that does not enclose
    its parts.
    """
    result = GeometryCheck()
    entries = list(models)
    assemblies = [(i, n, m) for i, n, m in entries if assembly_parts(m)]
    parts = [(i, n, m) for i, n, m in entries if not assembly_parts(m)]
    if len(parts) > 1 and not assemblies:
        result.evaluated += 1
        names = ", ".join(sorted(n for _, n, _ in parts))
        result.violations.append(f"multi-part design has no assembly (parts: {names})")
        return result
    if not assemblies:
        return result
    for _, aname, ameta in assemblies:
        refs = assembly_parts(ameta)
        ref_ids = {str(p.get("node_id")) for p in refs}
        ref_names = {str(p.get("name")) for p in refs}
        missing = [n for i, n, _ in parts if i not in ref_ids and n not in ref_names]
        result.evaluated += 1
        if missing and len(parts) > 1 and aname == assemblies[-1][1]:
            result.violations.append(
                f"multi-part design has no assembly covering: {', '.join(sorted(missing))}"
            )
        boxes: list[tuple[str, Any]] = []
        for p in refs:
            b = _bounds(p.get("position_bbox_mm"))
            if b is not None:
                boxes.append((str(p.get("name") or p.get("node_id") or "part"), b))
        for a in range(len(boxes)):
            for c in range(a + 1, len(boxes)):
                (na, (alo, ahi)), (nc, (clo, chi)) = boxes[a], boxes[c]
                depth = [min(ahi[k], chi[k]) - max(alo[k], clo[k]) for k in range(3)]
                if all(d > TOUCH_TOLERANCE_MM for d in depth):
                    result.violations.append(
                        f"assembly '{aname}': parts '{na}' and '{nc}' overlap "
                        f"({depth[0]:g} x {depth[1]:g} x {depth[2]:g} mm)"
                    )
        outer = _bounds(ameta.get("bbox_mm"))
        if outer is not None and boxes:
            lo = [min(b[0][k] for _, b in boxes) for k in range(3)]
            hi = [max(b[1][k] for _, b in boxes) for k in range(3)]
            tol = TOUCH_TOLERANCE_MM
            if any(outer[0][k] > lo[k] + tol or outer[1][k] < hi[k] - tol for k in range(3)):
                result.violations.append(
                    f"assembly '{aname}': its bounding box does not enclose its parts"
                )
    return result
