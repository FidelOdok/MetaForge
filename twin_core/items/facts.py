"""Comparable facts of a revision: what the brief shows, what a diff compares (FORGE-530).

The brief prints these per item (``bbox 120x40x8 mm; PLA; volume 1.2e+04
mm3``) and the rework helper diffs them between a rejected revision and the
one before it. One reader for both, so the numbers a rework prompt quotes are
the numbers the brief showed. Pure functions over node metadata, plus one
read of a constraint set's bound Constraint nodes.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from twin_core.models.enums import EdgeType

__all__ = [
    "cad_facts",
    "constraint_nodes",
    "constraint_value",
    "fmt_number",
    "format_bbox",
    "num",
    "render_cad_facts",
]


def _value(raw: Any) -> str:
    if isinstance(raw, float):
        return f"{raw:.4g}"
    return str(raw)


def num(raw: Any) -> float | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int | float):
        return float(raw)
    try:
        return float(str(raw))
    except (TypeError, ValueError):
        return None


def format_bbox(raw: Any) -> str | None:
    """``120x40x8 mm`` from the bbox shapes the recorders store, else ``None``."""
    dims: list[float | None] = []
    if isinstance(raw, list | tuple) and len(raw) == 3:
        dims = [num(v) for v in raw]
    elif isinstance(raw, list | tuple) and len(raw) == 6:
        lo, hi = raw[:3], raw[3:]
        dims = [
            (b - a) if a is not None and b is not None else None
            for a, b in zip((num(v) for v in lo), (num(v) for v in hi), strict=True)
        ]
    elif isinstance(raw, dict):
        lower = {str(k).lower(): v for k, v in raw.items()}
        for axis in ("x", "y", "z"):
            length = None
            for key in (axis, f"{axis}_length", f"{axis}length", f"size_{axis}", f"d{axis}"):
                if key in lower:
                    length = num(lower[key])
                    break
            if length is None and f"{axis}min" in lower and f"{axis}max" in lower:
                lo_v, hi_v = num(lower[f"{axis}min"]), num(lower[f"{axis}max"])
                if lo_v is not None and hi_v is not None:
                    length = hi_v - lo_v
            if length is None and f"{axis}_min" in lower and f"{axis}_max" in lower:
                lo_v, hi_v = num(lower[f"{axis}_min"]), num(lower[f"{axis}_max"])
                if lo_v is not None and hi_v is not None:
                    length = hi_v - lo_v
            dims.append(length)
        if all(d is None for d in dims):
            for key in ("size", "dimensions", "extent"):
                if key in lower:
                    return format_bbox(lower[key])
    if len(dims) != 3 or any(d is None for d in dims):
        return None
    return "x".join(f"{d:.4g}" for d in dims if d is not None) + " mm"


def cad_facts(meta: dict[str, Any]) -> dict[str, Any]:
    """The comparable facts of a cad revision: bbox, material, volume, mass, parts."""
    features = meta.get("geometry_features") if isinstance(meta, dict) else None
    props = features.get("properties") if isinstance(features, dict) else None
    props = props if isinstance(props, dict) else {}
    out: dict[str, Any] = {}
    bbox = (
        props.get("bounding_box")
        or props.get("bbox_mm")
        or props.get("bbox")
        or meta.get("bbox_mm")
        or meta.get("bounding_box")
    )
    if bbox is not None:
        out["bbox"] = bbox
    material = meta.get("material") or props.get("material")
    if isinstance(material, dict):
        material = material.get("name")
    if material:
        out["material"] = str(material)
    for key in ("volume_mm3", "volume"):
        if num(props.get(key)) is not None:
            out["volume_mm3"] = num(props.get(key))
            break
    for key, scale in (("mass_kg", 1.0), ("mass", 1.0), ("mass_g", 0.001)):
        value = num(props.get(key))
        if value is not None:
            out["mass_kg"] = value * scale
            break
    parts = meta.get("parts")
    if isinstance(parts, list) and parts:
        out["parts"] = len(parts)
    return out


def fmt_number(value: float) -> str:
    """``10000`` not ``1e+04``; four significant digits below 1000."""
    return f"{value:.0f}" if abs(value) >= 1000 else f"{value:.4g}"


def render_cad_facts(facts: dict[str, Any]) -> list[str]:
    out: list[str] = []
    bbox = format_bbox(facts.get("bbox"))
    if bbox:
        out.append(f"bbox {bbox}")
    if facts.get("material"):
        out.append(str(facts["material"]))
    if facts.get("volume_mm3") is not None:
        out.append(f"volume {fmt_number(facts['volume_mm3'])} mm3")
    if facts.get("mass_kg") is not None:
        out.append(f"mass {fmt_number(facts['mass_kg'])} kg")
    if facts.get("parts"):
        out.append(f"{facts['parts']} parts")
    return out


def constraint_value(node: Any) -> str | None:
    """``max_load_kg >= 20 kg`` for a bound constraint, else its name."""
    metric = getattr(node, "metric", None)
    operator = getattr(node, "operator", None)
    limit = getattr(node, "limit", None)
    if metric and operator and limit is not None:
        unit = getattr(node, "unit", None) or ""
        return f"{metric} {operator} {_value(limit)}{(' ' + unit) if unit else ''}"
    name = getattr(node, "name", None)
    return str(name) if name else None


async def constraint_nodes(twin: Any, set_node_id: UUID) -> list[Any]:
    """The Constraint nodes a constraint_set work product binds (CONSTRAINED_BY)."""
    edges = await twin.graph.get_edges(
        set_node_id, direction="outgoing", edge_type=EdgeType.CONSTRAINED_BY
    )
    nodes = []
    for edge in edges:
        node = await twin.graph.get_node(edge.target_id)
        if node is not None:
            nodes.append(node)
    return nodes
