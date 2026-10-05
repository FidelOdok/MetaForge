"""Compare two revisions of one item (FORGE-526).

``GET /v1/twin/items/{key}/diff?a=&b=`` (and ``forge twin diff KEY @a @b``)
returns, for ``KEY@a`` vs ``KEY@b``:

- **geometry**: bounding box, volume and mass. When the gateway's geometry
  differ is configured (FORGE-301, a real ``freecad.describe_step_file`` on
  both STEP files) it is used with the two revision nodes; otherwise, or when
  it fails, the measurements each revision recorded at commit time
  (``metadata.geometry_features.properties``) are compared. ``source`` says
  which. Mass is the recorded mass, else volume times the density of the
  recorded material when that material is known; it is never guessed.
- **parameters**: the generation parameters
  (``metadata.geometry_features.parameters``) that changed, were added or
  were removed, the same comparison the FORGE-270 feature diff makes.
- **requirements**: for a constraint set, each constraint's value
  (``operator limit unit``, else its expression) matched by name.
- **fields**: other top-level scalar fields that differ (statement, title,
  part number, quantity, ...), so intent, needs and BOM rows diff too.
- **dependents**: records and baselines still pinned to the old revision
  (simulation results, evidence, decisions with an edge or a source id
  naming it), which is what goes out of date when ``b`` becomes current.

Read-only.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog

from observability.tracing import get_tracer
from twin_core.items import UnknownItemError, find_item, parse_item_ref
from twin_core.items.current import (
    STRUCTURAL_EDGES,
    RevisionState,
    analysed_node_ids,
    current_revision,
    is_current_status,
    project_records,
    revision_states,
)
from twin_core.models.enums import EdgeType

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.item_diff")

_GEOMETRY_TYPES = frozenset({"cad_model", "assembly"})
_SKIP_FIELDS = frozenset(
    {
        "id",
        "node_type",
        "project_id",
        "created_at",
        "updated_at",
        "priced_at",
        "metadata",
        "content_hash",
        "file_path",
        "revision",
        "created_by",
    }
)
_MASS_KEYS = (("mass_kg", 1.0), ("mass_g", 0.001), ("mass", 1.0))
_VOLUME_KEYS = ("volume_mm3", "volume")
_BBOX_KEYS = ("bounding_box", "bbox_mm", "bbox")


def parse_revision(raw: str | int | None) -> int | None:
    """``"3"``, ``"@3"``, ``"KEY@3"`` or ``3`` -> ``3``; empty -> ``None``."""
    if raw is None or raw == "":
        return None
    if isinstance(raw, int):
        return raw
    text = str(raw).strip()
    if "@" in text:
        text = text.rpartition("@")[2]
    try:
        value = int(text)
    except ValueError as exc:
        raise ValueError(f"revision {raw!r} is not a number such as 3 or @3") from exc
    if value < 1:
        raise ValueError("revisions start at 1")
    return value


def _props(node: Any) -> dict[str, Any]:
    meta = getattr(node, "metadata", None) or {}
    features = meta.get("geometry_features") or {}
    props = dict(features.get("properties") or {})
    for key in ("bbox_mm", "material"):
        if key in meta and key not in props:
            props[key] = meta[key]
    return props


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first(props: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if props.get(key) is not None:
            return props[key]
    return None


def _mass_kg(props: dict[str, Any], volume_mm3: float | None) -> tuple[float | None, str | None]:
    for key, scale in _MASS_KEYS:
        value = _num(props.get(key))
        if value is not None:
            return value * scale, "recorded"
    material = props.get("material")
    if volume_mm3 is None or not isinstance(material, str) or not material:
        return None, None
    try:
        from tool_registry.tools.cadquery.materials import MATERIAL_DENSITY_KG_M3
    except ImportError:  # pragma: no cover -- ships with the gateway
        return None, None
    density = MATERIAL_DENSITY_KG_M3.get(material.strip().lower().replace(" ", "_"))
    if density is None:
        return None, None
    return volume_mm3 * 1e-9 * density, f"volume x {material} density"


def _delta(a: float | None, b: float | None) -> float | None:
    return round(b - a, 6) if a is not None and b is not None else None


def _geometry_block(
    a_props: dict[str, Any], b_props: dict[str, Any], live: dict[str, Any] | None
) -> dict[str, Any]:
    if live is not None:
        vol_a = _num(live.get("previous_volume_mm3"))
        vol_b = _num(live.get("current_volume_mm3"))
        bbox_a = live.get("previous_bounding_box")
        bbox_b = live.get("current_bounding_box")
        source = "describe_step_file"
    else:
        vol_a = _num(_first(a_props, _VOLUME_KEYS))
        vol_b = _num(_first(b_props, _VOLUME_KEYS))
        bbox_a = _first(a_props, _BBOX_KEYS)
        bbox_b = _first(b_props, _BBOX_KEYS)
        source = "recorded"
    mass_a, mass_src_a = _mass_kg(a_props, vol_a)
    mass_b, mass_src_b = _mass_kg(b_props, vol_b)
    available = any(v is not None for v in (vol_a, vol_b, bbox_a, bbox_b, mass_a, mass_b))
    return {
        "available": available,
        "source": source if available else None,
        "a": {"volume_mm3": vol_a, "bounding_box": bbox_a, "mass_kg": mass_a},
        "b": {"volume_mm3": vol_b, "bounding_box": bbox_b, "mass_kg": mass_b},
        "volume_delta_mm3": _delta(vol_a, vol_b),
        "mass_delta_kg": _delta(mass_a, mass_b),
        "mass_source": mass_src_b or mass_src_a,
        "bounding_box_delta": _bbox_delta(bbox_a, bbox_b),
    }


def _bbox_delta(a: Any, b: Any) -> dict[str, float] | None:
    if not isinstance(a, dict) or not isinstance(b, dict):
        return None
    out: dict[str, float] = {}
    for key in sorted(a.keys() & b.keys()):
        av, bv = _num(a[key]), _num(b[key])
        if av is not None and bv is not None and av != bv:
            out[key] = round(bv - av, 6)
    return out


def _dict_changes(a: dict[str, Any], b: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for key in sorted(a.keys() | b.keys()):
        if key not in b:
            out.append({"name": key, "status": "removed", "from": a[key], "to": None})
        elif key not in a:
            out.append({"name": key, "status": "added", "from": None, "to": b[key]})
        elif a[key] != b[key]:
            out.append({"name": key, "status": "changed", "from": a[key], "to": b[key]})
    return out


def _params(node: Any) -> dict[str, Any]:
    meta = getattr(node, "metadata", None) or {}
    return dict((meta.get("geometry_features") or {}).get("parameters") or {})


def _scalar_fields(node: Any) -> dict[str, Any]:
    if node is None or not hasattr(node, "model_dump"):
        return {}
    data = node.model_dump(mode="json")
    return {
        k: v
        for k, v in data.items()
        if k not in _SKIP_FIELDS and (v is None or isinstance(v, str | int | float | bool))
    }


def _constraint_value(c: Any) -> str:
    limit = getattr(c, "limit", None)
    if getattr(c, "metric", "") and limit is not None:
        shown = f"{limit:g}" if isinstance(limit, int | float) else str(limit)
        parts = [str(getattr(c, "operator", "") or ""), shown, str(getattr(c, "unit", "") or "")]
        return " ".join(p for p in parts if p)
    return str(getattr(c, "expression", "") or "")


async def _constraints_of(twin: Any, set_node_id: UUID) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for edge in await twin.graph.get_edges(
        set_node_id, direction="outgoing", edge_type=EdgeType.CONSTRAINED_BY
    ):
        node = await twin.graph.get_node(edge.target_id)
        name = getattr(node, "name", None)
        if isinstance(name, str):
            out[name] = node
    return out


async def _requirement_changes(twin: Any, a_id: UUID, b_id: UUID) -> list[dict[str, Any]]:
    left = await _constraints_of(twin, a_id)
    right = await _constraints_of(twin, b_id)
    out: list[dict[str, Any]] = []
    for name in sorted(left.keys() | right.keys()):
        old, new = left.get(name), right.get(name)
        old_v = _constraint_value(old) if old is not None else None
        new_v = _constraint_value(new) if new is not None else None
        old_sev = str(getattr(getattr(old, "severity", None), "value", "")) if old else None
        new_sev = str(getattr(getattr(new, "severity", None), "value", "")) if new else None
        if old is None:
            status = "added"
        elif new is None:
            status = "removed"
        elif old_v != new_v or old_sev != new_sev:
            status = "changed"
        else:
            continue
        out.append(
            {
                "name": name,
                "status": status,
                "from": old_v,
                "to": new_v,
                "from_severity": old_sev,
                "to_severity": new_sev,
                "unit": getattr(new or old, "unit", "") or "",
            }
        )
    return out


def _node_label(node: Any) -> tuple[str, str]:
    name = ""
    for attr in ("name", "title", "statement", "part_number"):
        value = getattr(node, attr, None)
        if isinstance(value, str) and value:
            name = value
            break
    kind = getattr(node, "type", None) or getattr(node, "entity_type", None)
    kind = getattr(kind, "value", kind) or getattr(getattr(node, "node_type", None), "value", "")
    return name, str(kind)


async def dependents_of(twin: Any, node_id: UUID, project_id: UUID | None) -> list[dict[str, Any]]:
    """Records and baselines pinned to ``node_id`` (one revision of an item)."""
    seen: dict[UUID, dict[str, Any]] = {}
    for edge in await twin.graph.get_edges(node_id, direction="incoming"):
        if edge.edge_type in STRUCTURAL_EDGES and edge.edge_type != EdgeType.INCLUDED_IN_BASELINE:
            continue
        node = await twin.graph.get_node(edge.source_id)
        if node is None:
            continue
        name, kind = _node_label(node)
        seen.setdefault(
            edge.source_id,
            {
                "node_id": str(edge.source_id),
                "name": name or kind,
                "type": kind,
                "via": str(getattr(edge.edge_type, "value", edge.edge_type)),
            },
        )
    for node in await project_records(twin, project_id):
        if node.id in seen:
            continue
        if node_id in await analysed_node_ids(twin, node):
            name, kind = _node_label(node)
            seen[node.id] = {
                "node_id": str(node.id),
                "name": name or kind,
                "type": kind,
                "via": "metadata",
            }
    return sorted(seen.values(), key=lambda d: (d["type"], d["name"]))


def _state_summary(state: RevisionState) -> dict[str, Any]:
    return {
        "revision": state.revision,
        "node_id": str(state.node_id),
        "status": state.status,
        "name": state.name,
        "run_id": state.run_id,
        "change_reason": state.change_reason,
        "created_at": state.created_at.isoformat() if state.created_at else None,
    }


def make_item_differ(twin: Any, *, geometry_diff_provider: Any = None) -> Any:
    """Return the async ``diff(key, a=None, b=None, project_id=None) -> dict``.

    ``geometry_diff_provider`` returns the gateway's FORGE-301 differ or
    ``None``; it is read per call, so a differ wired in after startup is used.
    """

    async def diff(
        key: str,
        a: str | int | None = None,
        b: str | int | None = None,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        with tracer.start_as_current_span("twin.item_diff") as span:
            span.set_attribute("twin.item_key", key)
            bare, pinned = parse_item_ref(key)
            item = await find_item(twin, bare, project_id, any_project=True)
            if item is None:
                raise UnknownItemError(f"no item {bare}")
            states = await revision_states(twin, item)
            by_rev = {s.revision: s for s in states}
            current = current_revision(states, item.head_node_id)
            b_rev = parse_revision(b) or pinned or (current.revision if current else None)
            if b_rev is None or b_rev not in by_rev:
                raise UnknownItemError(f"{item.key} has no revision @{b_rev}")
            a_rev = parse_revision(a)
            if a_rev is None:
                earlier = [
                    s.revision for s in states if s.revision < b_rev and is_current_status(s.status)
                ]
                if not earlier:
                    raise ValueError(
                        f"{item.key}@{b_rev} has no earlier revision to compare with; pass a="
                    )
                a_rev = earlier[-1]
            if a_rev not in by_rev:
                raise UnknownItemError(f"{item.key} has no revision @{a_rev}")
            state_a, state_b = by_rev[a_rev], by_rev[b_rev]
            node_a = await twin.graph.get_node(state_a.node_id)
            node_b = await twin.graph.get_node(state_b.node_id)
            span.set_attribute("twin.item_diff.a", a_rev)
            span.set_attribute("twin.item_diff.b", b_rev)

            geometry: dict[str, Any] | None = None
            warnings: list[str] = []
            if item.item_type in _GEOMETRY_TYPES:
                live: dict[str, Any] | None = None
                differ = geometry_diff_provider() if geometry_diff_provider else None
                if differ is not None and a_rev != b_rev:
                    try:
                        live = await differ(
                            work_product_id=str(state_b.node_id),
                            previous_work_product_id=str(state_a.node_id),
                        )
                    except Exception as exc:  # noqa: BLE001 -- fall back to recorded numbers
                        warnings.append(f"live geometry diff unavailable: {exc}")
                        logger.info(
                            "item_diff_geometry_fallback", item_key=item.key, error=str(exc)
                        )
                geometry = _geometry_block(_props(node_a), _props(node_b), live)

            requirements: list[dict[str, Any]] = []
            if item.item_type == "constraint_set":
                requirements = await _requirement_changes(twin, state_a.node_id, state_b.node_id)

            parameters = _dict_changes(_params(node_a), _params(node_b))
            fields = _dict_changes(_scalar_fields(node_a), _scalar_fields(node_b))
            dependents = (
                await dependents_of(twin, state_a.node_id, item.project_id)
                if a_rev != b_rev
                else []
            )
            logger.info(
                "item_diff_computed",
                item_key=item.key,
                a=a_rev,
                b=b_rev,
                parameters=len(parameters),
                requirements=len(requirements),
                dependents=len(dependents),
                geometry_source=(geometry or {}).get("source"),
            )
            return {
                "key": item.key,
                "item_type": item.item_type,
                "name": item.name,
                "a": _state_summary(state_a),
                "b": _state_summary(state_b),
                "a_ref": f"{item.key}@{a_rev}",
                "b_ref": f"{item.key}@{b_rev}",
                "geometry": geometry,
                "parameters": parameters,
                "requirements": requirements,
                "fields": fields,
                "dependents": dependents,
                "warnings": warnings,
            }

    return diff
