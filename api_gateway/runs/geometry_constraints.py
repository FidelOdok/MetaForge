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


def check_geometry_constraints(
    constraints: Iterable[Any], cad_models: Iterable[tuple[str, dict[str, Any]]]
) -> GeometryCheck:
    """Evaluate structured geometry constraints against ``(name, metadata)`` cad_models."""
    result = GeometryCheck()
    models = list(cad_models)
    for constraint in constraints:
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
