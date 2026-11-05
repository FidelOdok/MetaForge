"""DFM finding sets in the twin: gather, evaluate, store, recompute, decide (C3D slice 4).

For each component of a project this gathers the **best available geometry**
(an exact validated CAD revision, a CAD revision's generation parameters, a
parametric sketch's dimensions a person typed, or the concept block layout's
estimated sizes) and the **manufacturing context** it is made under, runs the
process profile's heuristics (:mod:`twin_core.dfm.heuristics`) and stores the
result as one ``dfm_finding_set`` record per component and evaluation:

* pinned (``KEY@n``) to the geometry item revisions and the manufacturing
  context revision it read, so when either moves on the existing record pins
  mark it ``STALE`` and the impact walk reaches it through ``dfm_constrains``;
* a recompute of the same component under the same profile **supersedes** the
  old set; one under another profile (the process changed) marks the old set
  **stale** with the reason and leaves it browsable (3D-RC-013);
* the same inputs reuse the current set instead of writing a new one.

**Incremental** (DFM-011, 3D-RC-014). :func:`on_head_moved` runs after the
twin's own head-move work (staleness, impact, the face remap): only the
components whose finding sets were pinned to the moved item are recomputed.
:func:`on_context_written` runs after a manufacturing context write: when a
field the rules read changed (process, material, quantity, machine), the sets
under that context are recomputed; otherwise the sets its move marked stale
are revalidated, since nothing they read changed.

**Truthful status** (DFM-013). A set is ``issues_found``,
``preliminary_guidance``, ``analysis_pending`` or ``not_assessed``, never a
pass. The geometry outputs it was computed on carry the set's ``dfm_status``
and note, only when a rule actually ran.

**Decisions** (DFM-015). ``keep_design`` records the person's choice on the
finding and carries it to later recomputes; ``try_fix`` returns the fix as a
proposal, and when the fix would change a protected requirement (a field a
person stated in the manufacturing context, or a protected reference feature)
it files a design-change approval instead; ``explore_process`` previews the
component under the other profiles and writes nothing.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

import structlog
from pydantic import BaseModel, Field

from observability.tracing import get_tracer
from twin_core.dfm import (
    PROFILES,
    ComponentGeometry,
    DfmFindingSet,
    FaceSample,
    MachineCapability,
    MachineContext,
    Measure,
    ProcessContext,
    alternative_profiles,
    evaluate,
    product_status,
    profile_for_process,
    variant_guidance,
)
from twin_core.dfm.heuristics import VariantGuidance
from twin_core.manufacturing import DFM_STATUS_LABELS, Authority, DfmStatus
from twin_core.manufacturing.context import CONTEXT_FIELDS

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.dfm_findings")

RECORD_TYPE = "dfm_finding_set"
#: Where a finding set node keeps the set.
META_KEY = "dfm_finding_set"
#: Context fields DFM rules read; a change to any other field reaches nothing.
RELEVANT_CONTEXT_FIELDS: frozenset[str] = frozenset(
    {
        "selected_process",
        "process_candidates",
        "selected_material",
        "material_candidates",
        "quantity",
        "machine_or_supplier_profile",
        "tolerances",
    }
)
#: Item types whose head move changes a component's geometry.
GEOMETRY_ITEM_TYPES: frozenset[str] = frozenset({"cad_model", "assembly", "parametric_sketch"})
#: Output metadata keys this module writes as status tags.
OUTPUT_KEYS = ("dfm_status", "dfm_status_label", "manufacturing_note", "dfm_finding_set_id")

DecisionKind = Literal["keep_design", "try_fix", "explore_process"]

#: CAD generation parameters read as measures, by quantity.
_PARAMETER_QUANTITIES: dict[str, tuple[str, ...]] = {
    "wall_thickness": ("wall_thickness", "wall", "wall_mm", "shell_thickness", "min_wall"),
    "feature_size": ("feature_size", "min_feature", "min_feature_size"),
    "hole_diameter": ("hole_diameter", "hole_d", "bore_diameter", "hole_diameter_mm"),
    "bridge_length": ("bridge_length", "bridge_span", "span"),
    "internal_corner_radius": ("internal_corner_radius", "inside_corner_radius", "corner_radius"),
    "cavity_depth": ("cavity_depth", "pocket_depth"),
    "cavity_width": ("cavity_width", "pocket_width"),
    "draft_angle": ("draft_angle", "draft"),
    "rib_thickness": ("rib_thickness",),
    "bend_radius": ("bend_radius", "inside_bend_radius"),
    "flange_length": ("flange_length",),
    "hole_to_bend": ("hole_to_bend", "hole_to_bend_distance"),
    "sheet_thickness": ("sheet_thickness", "material_thickness", "gauge_mm"),
    "drain_hole_diameter": ("drain_hole_diameter", "drain_hole"),
}
_CAVITY_FLAGS = ("has_internal_cavity", "hollow", "shelled", "enclosed_cavity")
_DEG_QUANTITIES = frozenset({"draft_angle"})

_metrics: Any = None


def _collector() -> Any:
    global _metrics  # noqa: PLW0603
    if _metrics is None:
        from observability.metrics import collector_for

        _metrics = collector_for("metaforge-gateway")
    return _metrics


def _metric(name: str, *args: str) -> None:
    try:
        getattr(_collector(), name)(*args)
    except Exception as exc:  # noqa: BLE001 - a counter never fails an evaluation
        logger.debug("dfm_metric_failed", metric=name, error=str(exc))


class DfmError(ValueError):
    """A request that cannot be honoured; ``status`` is its HTTP status."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _pid(project_id: str | UUID) -> UUID:
    try:
        return project_id if isinstance(project_id, UUID) else UUID(str(project_id))
    except ValueError as exc:
        raise DfmError(f"project id {project_id!r} is not a UUID") from exc


def _wp_type(node: Any) -> str:
    t = getattr(node, "type", None)
    return str(getattr(t, "value", t) or "")


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


# ---------------------------------------------------------------------------
# Gathering geometry
# ---------------------------------------------------------------------------

_LEVEL_RANK = {"none": 0, "L1": 1, "parametric_sketch": 2, "parametric": 3, "exact": 4}


@dataclass
class _Source:
    level: str
    refs: list[str]
    #: ``KEY@n`` item revisions this source is (pinned on the set).
    pins: list[str]
    measures: list[Measure]
    faces: list[FaceSample] | None = None
    faces_confidence: str = "estimated"
    faces_directed: bool = False
    faces_ref: str | None = None
    #: Nodes whose dfm status this source's evaluation should update.
    output_nodes: list[str] = field(default_factory=list)


@dataclass
class Gathered:
    """One component, its best geometry and where it came from."""

    component_id: str
    name: str
    geometry: ComponentGeometry
    pins: list[str]
    output_nodes: list[str]


def _cad_measures(meta: dict[str, Any], ref: str, exact: bool) -> list[Measure]:
    out: list[Measure] = []
    features = _dict(meta.get("geometry_features"))
    props = _dict(features.get("properties"))
    bbox = props.get("bounding_box")
    if isinstance(bbox, dict):
        try:
            dims = [
                float(bbox["max_x"]) - float(bbox["min_x"]),
                float(bbox["max_y"]) - float(bbox["min_y"]),
                float(bbox["max_z"]) - float(bbox["min_z"]),
            ]
        except (KeyError, TypeError, ValueError):
            dims = []
        if len(dims) == 3 and min(dims) > 0:
            out.append(
                Measure(
                    quantity="envelope",
                    value=[round(d, 4) for d in dims],
                    confidence="exact" if exact else "design_parameter",
                    source="exact B-Rep bounding box" if exact else "CAD bounding box",
                    feature="bounding_box",
                    evidence_ref=ref,
                )
            )
    params = _dict(features.get("parameters"))
    lowered = {str(k).lower(): v for k, v in params.items()}
    for quantity, names in _PARAMETER_QUANTITIES.items():
        for name in names:
            value = _num(lowered.get(name))
            if value is None or value <= 0:
                continue
            out.append(
                Measure(
                    quantity=quantity,
                    value=value,
                    unit="deg" if quantity in _DEG_QUANTITIES else "mm",
                    confidence="design_parameter",
                    source="CAD parameter",
                    feature=name,
                    evidence_ref=ref,
                )
            )
            break
    for flag in _CAVITY_FLAGS:
        if isinstance(lowered.get(flag), bool):
            out.append(
                Measure(
                    quantity="has_internal_cavity",
                    value=bool(lowered[flag]),
                    unit="",
                    confidence="design_parameter",
                    source="CAD parameter",
                    feature=flag,
                    evidence_ref=ref,
                )
            )
            break
    return out


def _faces_from(raw: Any) -> list[FaceSample]:
    out: list[FaceSample] = []
    for i, face in enumerate(raw if isinstance(raw, list) else []):
        if not isinstance(face, dict):
            continue
        normal = face.get("normal")
        if not isinstance(normal, (list, tuple)) or len(normal) != 3:
            continue
        try:
            n = (float(normal[0]), float(normal[1]), float(normal[2]))
        except (TypeError, ValueError):
            continue
        out.append(
            FaceSample(
                face_id=str(face.get("face_id") or face.get("name") or f"face-{i + 1}")[:200],
                normal=n,
                area_mm2=_num(face.get("area_mm2", face.get("area"))),
                face_signature=(
                    str(face.get("semantic_tag") or face.get("face_signature") or "")[:300] or None
                ),
            )
        )
    return out


async def _overhang_faces(
    twin: Any, pid: UUID, refs: set[str]
) -> dict[str, tuple[list[FaceSample], str]]:
    """Face tables recorded by the overhang check, by the CAD ref they were pinned to."""
    from twin_core.consistency.record_pins import VALID_STATUSES, record_pins, record_staleness

    out: dict[str, tuple[list[FaceSample], str]] = {}
    list_entities = getattr(twin, "list_engineering_entities", None)
    if not callable(list_entities) or not refs:
        return out
    try:
        entities = await list_entities(project_id=pid, entity_type="evidence")
    except Exception as exc:  # noqa: BLE001 - no evidence reads as no faces
        logger.debug("dfm_overhang_evidence_unavailable", error=str(exc))
        return out
    for entity in entities:
        meta = dict(getattr(entity, "metadata", None) or {})
        result = _dict(meta.get("result"))
        if result.get("metric") != "overhang_check" or record_staleness(meta) not in VALID_STATUSES:
            continue
        faces = _faces_from(result.get("faces"))
        if not faces:
            continue
        for pin in record_pins(meta):
            ref = str(pin.get("item_ref") or "")
            if ref in refs:
                out[ref] = (faces, f"node:{entity.id}")
    return out


async def _layout_parts(twin: Any, render: Any) -> dict[str, dict[str, Any]]:
    """The block layout parts an L1 mesh was drawn from, by part name."""
    meta = dict(getattr(render, "metadata", None) or {})
    raw = meta.get("source_layout_node_id")
    if not raw:
        return {}
    try:
        layout_node = await twin.get_work_product(UUID(str(raw)))
    except Exception:  # noqa: BLE001 - a missing layout reads as no sizes
        return {}
    layout = (getattr(layout_node, "metadata", None) or {}).get("concept_layout")
    parts = layout.get("parts") if isinstance(layout, dict) else None
    return {str(p.get("name")): p for p in parts or [] if isinstance(p, dict) and p.get("name")}


def _part_size(part: dict[str, Any]) -> list[float] | None:
    size = part.get("size_mm")
    if isinstance(size, list) and len(size) == 3:
        try:
            return [float(v) for v in size]
        except (TypeError, ValueError):
            return None
    bbox = part.get("bbox_mm")
    if isinstance(bbox, list) and len(bbox) == 2:
        try:
            return [float(bbox[1][i]) - float(bbox[0][i]) for i in range(3)]
        except (TypeError, ValueError, IndexError):
            return None
    return None


async def gather_components(twin: Any, project_id: str | UUID) -> list[Gathered]:
    """Every known component with the best geometry it has. Never raises on one bad source."""
    from twin_core.component_identity import project_identity
    from twin_core.geometry_state import GeometryState, geometry_state_of
    from twin_core.items import list_items, supports_items
    from twin_core.models.enums import WorkProductType
    from twin_core.representation import (
        COMPONENT_ID_KEY,
        PART_COMPONENT_IDS_KEY,
        STRUCTURED_MESH_TOOL,
    )

    pid = _pid(project_id)
    names: dict[str, str] = {}
    sources: dict[str, list[_Source]] = {}
    ident = await project_identity(twin, pid)
    for cid, info in ident["components"].items():
        names[cid] = str(info.get("name") or cid)
        sources.setdefault(cid, [])

    def add(cid: str, name: str, source: _Source) -> None:
        names.setdefault(cid, name)
        sources.setdefault(cid, []).append(source)

    if supports_items(twin):
        cad_refs: dict[str, tuple[str, str, Any, dict[str, Any], bool]] = {}
        for item_type in ("cad_model", "assembly"):
            for item in await list_items(twin, project_id=pid, item_type=item_type):
                if item.head_node_id is None:
                    continue
                node = await twin.graph.get_node(item.head_node_id)
                meta = dict(getattr(node, "metadata", None) or {})
                if node is None or str(meta.get("model_role") or "") == "analysis":
                    continue
                cid = meta.get(COMPONENT_ID_KEY)
                if not cid and item_type == "assembly":
                    continue  # an assembly's members have no geometry of their own here
                cid = str(cid or f"item:{item.key}")
                state = geometry_state_of("cad_model", meta)
                exact = state in (GeometryState.VALIDATED, GeometryState.RELEASED)
                ref = f"{item.key}@{item.head_revision}"
                cad_refs[ref] = (cid, item.name, item.head_node_id, meta, exact)
        faces_by_ref = await _overhang_faces(twin, pid, set(cad_refs))
        for ref, (cid, name, node_id, meta, exact) in cad_refs.items():
            own_faces = _faces_from(meta.get("dfm_faces") or meta.get("face_table"))
            faces: list[FaceSample] = own_faces
            faces_ref: str | None = ref if own_faces else None
            if not own_faces and ref in faces_by_ref:
                faces, faces_ref = faces_by_ref[ref]
            add(
                cid,
                name,
                _Source(
                    level="exact" if exact else "parametric",
                    refs=[ref],
                    pins=[ref],
                    measures=_cad_measures(meta, ref, exact),
                    faces=faces or None,
                    # A gmsh face table has no outward sense; the conservative check runs.
                    faces_confidence="estimated",
                    faces_directed=bool(own_faces and meta.get("dfm_faces_directed")),
                    faces_ref=faces_ref,
                    output_nodes=[str(node_id)],
                ),
            )
        for item in await list_items(twin, project_id=pid, item_type="parametric_sketch"):
            if item.head_node_id is None:
                continue
            node = await twin.graph.get_node(item.head_node_id)
            meta = dict(getattr(node, "metadata", None) or {})
            cid = meta.get(COMPONENT_ID_KEY)
            if not cid:
                continue
            ref = f"{item.key}@{item.head_revision}"
            sketch_measures: list[Measure] = []
            for feature in meta.get("feature_plan") or []:
                if not isinstance(feature, dict) or feature.get("op") != "extrude":
                    continue
                depth = (feature.get("parameters") or {}).get("depth") or {}
                value = _num(depth.get("value")) if isinstance(depth, dict) else None
                if value is None or value <= 0 or depth.get("status") != "provided":
                    continue
                sketch_measures.append(
                    Measure(
                        quantity="wall_thickness",
                        value=value,
                        confidence="human_confirmed",
                        source="parametric sketch (depth a person gave)",
                        feature=str(feature.get("id") or "extrude"),
                        evidence_ref=ref,
                    )
                )
            add(str(cid), item.name, _Source("parametric_sketch", [ref], [ref], sketch_measures))

    try:
        renders = await twin.list_work_products(
            work_product_type=WorkProductType.RENDER, project_id=pid
        )
    except Exception as exc:  # noqa: BLE001 - no renders reads as no L1
        logger.debug("dfm_renders_unavailable", error=str(exc))
        renders = []
    l1 = [r for r in renders if (r.metadata or {}).get("generated_by") == STRUCTURED_MESH_TOOL]
    if l1:
        newest = max(l1, key=lambda r: r.created_at.isoformat() if r.created_at else "")
        members = (newest.metadata or {}).get(PART_COMPONENT_IDS_KEY) or {}
        parts = await _layout_parts(twin, newest)
        ref = f"node:{newest.id}"
        for part_name, cid in members.items() if isinstance(members, dict) else []:
            part = parts.get(str(part_name)) or {}
            size = _part_size(part)
            confidence = "human_confirmed" if part.get("provenance") == "verified" else "estimated"
            l1_measures: list[Measure] = []
            if size and min(size) > 0:
                l1_measures = [
                    Measure(
                        quantity="envelope",
                        value=[round(v, 4) for v in size],
                        confidence=confidence,  # type: ignore[arg-type]
                        source="concept layout (L1), sizes estimated",
                        feature="block",
                        evidence_ref=ref,
                    ),
                    Measure(
                        quantity="thinnest_section",
                        value=round(min(size), 4),
                        confidence=confidence,  # type: ignore[arg-type]
                        source="concept layout (L1), sizes estimated",
                        feature="block",
                        evidence_ref=ref,
                    ),
                ]
            add(
                str(cid),
                str(part_name),
                _Source("L1", [ref], [], l1_measures, output_nodes=[str(newest.id)]),
            )

    out: list[Gathered] = []
    for cid in sorted(sources, key=lambda c: (names.get(c) or c).lower()):
        ranked = sorted(sources[cid], key=lambda s: -_LEVEL_RANK.get(s.level, 0))
        best = ranked[0] if ranked else None
        merged: list[Measure] = []
        covered: set[str] = set()
        refs: list[str] = []
        pins: list[str] = []
        outputs: list[str] = []
        for src in ranked:
            fresh = [m for m in src.measures if m.quantity not in covered]
            if src is best or fresh:
                refs += src.refs
                pins += src.pins
            merged += fresh
            covered |= {m.quantity for m in src.measures}
        if best is not None:
            outputs = list(best.output_nodes)
        faced = next((s for s in ranked if s.faces), None)
        geometry = ComponentGeometry(
            component_id=cid,
            name=names.get(cid, cid),
            level=best.level if best else "none",  # type: ignore[arg-type]
            geometry_refs=list(dict.fromkeys(refs)),
            measures=merged,
            faces=faced.faces if faced else None,
            faces_confidence=faced.faces_confidence if faced else "estimated",  # type: ignore[arg-type]
            faces_directed=faced.faces_directed if faced else False,
            faces_evidence_ref=faced.faces_ref if faced else None,
        )
        out.append(
            Gathered(
                component_id=cid,
                name=names.get(cid, cid),
                geometry=geometry,
                pins=list(dict.fromkeys(pins)),
                output_nodes=outputs,
            )
        )
    return out


# ---------------------------------------------------------------------------
# The manufacturing context, as the evaluator reads it
# ---------------------------------------------------------------------------


def _machine_context(profile_value: Any) -> MachineContext | None:
    """The selected machine from the machine library; ``None`` when not found or none stated."""
    if not isinstance(profile_value, dict):
        return None
    wanted_id = str(profile_value.get("machine_id") or "").strip()
    wanted = str(profile_value.get("machine") or "").strip().lower()
    if not wanted_id and not wanted:
        return None
    try:
        from api_gateway.library.routes import get_machine_library
        from twin_core.library.machines import MachineInstance, MachineModel, effective_capabilities

        library = get_machine_library()
        found: Any = None
        if wanted_id:
            try:
                found = library.get(wanted_id)
            except Exception:  # noqa: BLE001 - try the name next
                found = None
        if found is None and wanted:
            for entry in library.list():
                label = (
                    entry.name
                    if isinstance(entry, MachineInstance)
                    else f"{entry.make} {entry.model}"
                )
                if label.strip().lower() == wanted or wanted in label.strip().lower():
                    found = entry
                    break
        if found is None:
            return None
        if isinstance(found, MachineInstance):
            model = None
            if found.model_id:
                try:
                    candidate = library.get(found.model_id)
                    model = candidate if isinstance(candidate, MachineModel) else None
                except Exception:  # noqa: BLE001
                    model = None
            caps = effective_capabilities(found, model)
            label = found.name
        else:
            caps = dict(found.capabilities)
            label = f"{found.make} {found.model}"
        return MachineContext(
            id=found.id,
            name=label,
            capabilities={
                key: MachineCapability(
                    value=cap.value,
                    source=f"{label} {key} ({cap.source_kind}"
                    + (f": {cap.source_ref}" if cap.source_ref else "")
                    + ")",
                )
                for key, cap in caps.items()
            },
        )
    except Exception as exc:  # noqa: BLE001 - an unreadable library reads as no machine
        logger.debug("dfm_machine_library_unavailable", error=str(exc))
        return None


def process_context(stored: Any) -> ProcessContext:
    """What the evaluator reads from a stored manufacturing context (or none)."""
    if stored is None:
        return ProcessContext(context_ref="unresolved", unknowns=["process", "material", "machine"])
    record = stored.record

    def pick(chosen: str, pool: str) -> tuple[str | None, str]:
        entry = record.get(chosen)
        if entry.known and not entry.stale:
            return str(entry.value), entry.authority.value
        cands = record.get(pool)
        if cands.known and not cands.stale and isinstance(cands.value, list) and cands.value:
            return str(cands.value[0]), "candidate"
        return None, "unknown"

    process, p_auth = pick("selected_process", "process_candidates")
    material, m_auth = pick("selected_material", "material_candidates")
    machine_field = record.get("machine_or_supplier_profile")
    machine = _machine_context(machine_field.value) if machine_field.confirmed else None
    quantity = record.quantity.value if record.quantity.known else None
    return ProcessContext(
        process=process,
        process_authority=p_auth,
        material=material,
        material_authority=m_auth,
        quantity=quantity if isinstance(quantity, (int, str)) else None,
        machine=machine,
        context_ref=stored.ref,
        unknowns=list(record.unknowns),
    )


async def _context_for(twin: Any, pid: UUID, gathered: Gathered) -> Any:
    from api_gateway.twin.manufacturing_context import (
        ManufacturingContextError,
        effective_context,
    )

    for key in (gathered.name, gathered.component_id):
        try:
            own = await effective_context(twin, pid, key)
        except ManufacturingContextError:
            continue
        if own is not None:
            return own
    return None


# ---------------------------------------------------------------------------
# Storing finding sets
# ---------------------------------------------------------------------------


async def _sets(twin: Any, pid: UUID) -> list[Any]:
    from twin_core.models.enums import WorkProductType

    try:
        nodes = await twin.list_work_products(
            work_product_type=WorkProductType.DFM_FINDING_SET, project_id=pid
        )
    except Exception as exc:  # noqa: BLE001 - no list API reads as no sets
        logger.debug("dfm_sets_unavailable", error=str(exc))
        return []
    return sorted(nodes, key=lambda n: n.created_at.isoformat() if n.created_at else "")


def set_of(node: Any) -> DfmFindingSet | None:
    raw = (getattr(node, "metadata", None) or {}).get(META_KEY)
    if not isinstance(raw, dict):
        return None
    try:
        return DfmFindingSet.model_validate(raw)
    except ValueError as exc:
        logger.warning("dfm_finding_set_unreadable", node_id=str(node.id), error=str(exc))
        return None


def _staleness(node: Any) -> str:
    from twin_core.consistency.record_pins import record_staleness

    return record_staleness(getattr(node, "metadata", None) or {})


def _valid(node: Any) -> bool:
    from twin_core.consistency.record_pins import VALID_STATUSES

    return _staleness(node) in VALID_STATUSES


def _decisions_for(nodes: list[Any], component_id: str) -> dict[str, dict[str, Any]]:
    """A person's decisions on this component's findings, newest winning."""
    out: dict[str, dict[str, Any]] = {}
    for node in nodes:
        meta = node.metadata or {}
        fs = _dict(meta.get(META_KEY))
        if fs.get("component_id") != component_id:
            continue
        for fid, decision in (meta.get("dfm_decisions") or {}).items():
            if isinstance(decision, dict):
                out[str(fid)] = decision
    return out


def _set_name(finding_set: DfmFindingSet) -> str:
    profile = finding_set.profile_id or "no process"
    return f"DFM findings: {finding_set.component_name or finding_set.component_id} ({profile})"


async def _annotate_outputs(
    twin: Any, gathered: Gathered, finding_set: DfmFindingSet, set_id: str
) -> None:
    """Write the set's status and note on the outputs it was computed on (status tags).

    Only when a rule ran: a set that assessed nothing leaves outputs as they are.
    """
    from twin_core.geometry_state import set_node_metadata

    if not finding_set.rules_run and not finding_set.findings:
        return
    for raw in gathered.output_nodes:
        try:
            node = await twin.graph.get_node(UUID(raw))
            if node is None:
                continue
            meta = dict(node.metadata or {})
            fields: dict[str, Any]
            if _wp_type(node) == "render":
                # One L1 mesh shows every component: keep each one's status.
                per = dict(meta.get("dfm_components") or {})
                per[finding_set.component_id] = {
                    "dfm_status": finding_set.status.value,
                    "manufacturing_note": finding_set.note,
                    "dfm_finding_set_id": set_id,
                }
                worst = product_status([DfmStatus(v["dfm_status"]) for v in per.values()])
                notes = [
                    v["manufacturing_note"] for v in per.values() if v["dfm_status"] == worst.value
                ]
                fields = {
                    "dfm_components": per,
                    "dfm_status": worst.value,
                    "dfm_status_label": DFM_STATUS_LABELS[worst],
                    "manufacturing_note": notes[0] if len(notes) == 1 else _plural_note(worst, per),
                }
            else:
                fields = {
                    "dfm_status": finding_set.status.value,
                    "dfm_status_label": finding_set.status_label,
                    "manufacturing_note": finding_set.note,
                    "dfm_finding_set_id": set_id,
                }
            await set_node_metadata(twin, node, **fields)
        except Exception as exc:  # noqa: BLE001 - a tag never fails an evaluation
            logger.warning("dfm_output_annotate_failed", node_id=raw, error=str(exc))


def _plural_note(status: DfmStatus, per: dict[str, Any]) -> str:
    if status is DfmStatus.ISSUES_FOUND:
        n = sum(1 for v in per.values() if v["dfm_status"] == status.value)
        return f"Manufacturing suggestions for {n} parts (preliminary)"
    return DFM_STATUS_LABELS[status]


async def _store(
    twin: Any,
    pid: UUID,
    gathered: Gathered,
    finding_set: DfmFindingSet,
    *,
    context_ref: str | None,
    trigger: str,
    existing: list[Any],
) -> tuple[Any, bool]:
    """Store ``finding_set`` (or reuse an identical current one). ``(node, created)``."""
    from api_gateway.twin.record_pins import link_record, pin_metadata, resolve_record_pins
    from twin_core.consistency.record_pins import set_record_status
    from twin_core.consistency.staleness import StalenessStatus
    from twin_core.models.enums import WorkProductType
    from twin_core.models.work_product import WorkProduct

    mine = [
        n
        for n in existing
        if ((n.metadata or {}).get(META_KEY) or {}).get("component_id") == gathered.component_id
    ]
    for node in reversed(mine):
        meta = node.metadata or {}
        fs = meta.get(META_KEY) or {}
        if (
            _valid(node)
            and fs.get("inputs_sha256") == finding_set.inputs_sha256
            and fs.get("profile_id") == finding_set.profile_id
        ):
            return node, False

    refs = list(gathered.pins)
    if context_ref and "@" in context_ref:
        refs.append(context_ref)
    try:
        pins = await resolve_record_pins(twin, depends_on=refs, project_id=pid)
    except Exception as exc:  # noqa: BLE001 - a moved ref pins nothing rather than failing
        logger.warning("dfm_pins_unresolved", refs=refs, error=str(exc))
        pins = []
    name = _set_name(finding_set)
    body = finding_set.model_dump(mode="json")
    metadata: dict[str, Any] = {
        META_KEY: body,
        "component_id": finding_set.component_id,
        "process": finding_set.process,
        "profile_id": finding_set.profile_id,
        "profile_version": finding_set.profile_version,
        "dfm_status": finding_set.status.value,
        "dfm_status_label": finding_set.status_label,
        "manufacturing_note": finding_set.note,
        "context_ref": context_ref or "unresolved",
        "geometry_refs": list(finding_set.geometry_refs),
        "inputs_sha256": finding_set.inputs_sha256,
        "trigger": trigger,
        "dfm_decisions": {
            f.id: f.decision for f in finding_set.findings if isinstance(f.decision, dict)
        },
        # Heuristic guidance never reads as a pass in the shared status vocabulary.
        "result": {"verdict": "incomplete", "heuristic_only": True},
        "authority": "heuristic",
    }
    metadata.update(pin_metadata(pins))
    now = datetime.now(UTC)
    wp_id = uuid4()
    created = await twin.create_work_product(
        WorkProduct(
            id=wp_id,
            name=name,
            type=WorkProductType.DFM_FINDING_SET,
            domain="manufacturing",
            file_path="",
            content_hash=hashlib.sha256(
                json.dumps(body, sort_keys=True, default=str).encode("utf-8")
            ).hexdigest(),
            format="json",
            metadata=metadata,
            created_at=now,
            updated_at=now,
            created_by="metaforge:dfm",
            project_id=pid,
        )
    )
    node_id = getattr(created, "id", wp_id)
    await link_record(twin, node_id, pins, record_type=RECORD_TYPE, name=name, metadata=metadata)
    # Older sets of this component: the same profile is superseded by this
    # one; another profile's set is out of date for the process now chosen.
    for node in mine:
        if not _valid(node) or node.id == node_id:
            continue
        other = (node.metadata or {}).get(META_KEY) or {}
        try:
            if other.get("profile_id") == finding_set.profile_id:
                await set_record_status(
                    twin,
                    node.id,
                    StalenessStatus.SUPERSEDED,
                    f"recomputed as {node_id}",
                )
            else:
                await set_record_status(
                    twin,
                    node.id,
                    StalenessStatus.STALE,
                    f"it was for {other.get('process') or 'no process'}; the process is now "
                    f"{finding_set.process or 'unknown'}",
                )
        except Exception as exc:  # noqa: BLE001 - the new set is saved
            logger.warning("dfm_older_set_status_failed", node_id=str(node.id), error=str(exc))
    return (await twin.get_work_product(node_id)) or created, True


def _publish(pid: UUID, op_suffix: str, message: str, *, failed: bool = False) -> None:
    from api_gateway.operations import publisher as operations

    op = f"job:dfm-{str(pid)[:8]}-{op_suffix}"
    events = (
        (("accepted", "running"), ("failed", "failed"))
        if failed
        else (("accepted", "running"), ("completed", "completed"))
    )
    for event, state in events:
        operations.publish(
            str(pid),
            op,
            event,
            state,
            event_id=f"{op}-{event}",
            domain="manufacturing",
            title="Manufacturing guidance",
            message=(message if event != "accepted" else "Checking manufacturing guidance")[:500],
        )


_locks: dict[str, asyncio.Lock] = {}


def _lock(pid: UUID) -> asyncio.Lock:
    lock = _locks.get(str(pid))
    if lock is None:
        lock = _locks[str(pid)] = asyncio.Lock()
    return lock


async def evaluate_project(
    twin: Any,
    project_id: str | UUID,
    *,
    component_ids: list[str] | None = None,
    trigger: str = "manual",
    exact_checks_scheduled: bool = False,
) -> list[tuple[Gathered, Any, DfmFindingSet, bool]]:
    """Evaluate (some of) a project's components; store a set per component.

    Returns ``[(gathered, node, set, created)]``. ``created`` is false when an
    identical current set was reused.
    """
    pid = _pid(project_id)
    async with _lock(pid):
        with tracer.start_as_current_span("twin.dfm.evaluate_project") as span:
            span.set_attribute("project_id", str(pid))
            span.set_attribute("dfm.trigger", trigger)
            try:
                gathered = await gather_components(twin, pid)
                if component_ids is not None:
                    wanted = set(component_ids)
                    gathered = [g for g in gathered if g.component_id in wanted]
                existing = await _sets(twin, pid)
                out: list[tuple[Gathered, Any, DfmFindingSet, bool]] = []
                for g in gathered:
                    stored = await _context_for(twin, pid, g)
                    ctx = process_context(stored)
                    fs = evaluate(
                        g.geometry,
                        ctx,
                        exact_checks_scheduled=exact_checks_scheduled,
                        decisions=_decisions_for(existing, g.component_id),
                    )
                    node, created = await _store(
                        twin,
                        pid,
                        g,
                        fs,
                        context_ref=stored.ref if stored is not None else None,
                        trigger=trigger,
                        existing=existing,
                    )
                    if created:
                        existing.append(node)
                        await _annotate_outputs(twin, g, fs, str(node.id))
                        for f in fs.findings:
                            _metric("record_dfm_finding", f.severity, fs.profile_id or "none")
                    out.append((g, node, fs, created))
                span.set_attribute("dfm.components", len(out))
                _metric("record_dfm_evaluation", trigger, "evaluated")
                written = sum(1 for o in out if o[3])
                issues = sum(len(o[2].findings) for o in out)
                logger.info(
                    "dfm_project_evaluated",
                    project_id=str(pid),
                    trigger=trigger,
                    components=len(out),
                    written=written,
                    findings=issues,
                )
                if out:
                    _publish(
                        pid,
                        uuid4().hex[:8],
                        f"Manufacturing guidance checked for {len(out)} part(s): "
                        f"{issues} suggestion(s); {written} updated",
                    )
                return out
            except Exception as exc:
                span.record_exception(exc)
                _metric("record_dfm_evaluation", trigger, "failed")
                logger.warning(
                    "dfm_evaluation_failed", project_id=str(pid), trigger=trigger, error=str(exc)
                )
                _publish(
                    pid, uuid4().hex[:8], "Manufacturing guidance could not be checked", failed=True
                )
                raise


# ---------------------------------------------------------------------------
# Incremental recompute
# ---------------------------------------------------------------------------


def _pinned_keys(node: Any) -> set[str]:
    from twin_core.consistency.record_pins import record_pins

    return {str(p.get("item_key")) for p in record_pins(node.metadata or {})}


async def on_head_moved(twin: Any, item: Any, head_revision: int, head_node_id: Any) -> None:
    """After a geometry head move: recompute only the components it affects. Never raises."""
    if str(getattr(item, "item_type", "") or "") not in GEOMETRY_ITEM_TYPES:
        return
    project_id = getattr(item, "project_id", None)
    if project_id is None:
        return
    try:
        nodes = await _sets(twin, _pid(project_id))
        affected = sorted(
            {
                str(((n.metadata or {}).get(META_KEY) or {}).get("component_id"))
                for n in nodes
                if item.key in _pinned_keys(n) and _staleness(n) == "stale"
            }
        )
        if not affected:
            return
        _metric("record_dfm_stale_recompute", "geometry")
        logger.info(
            "dfm_recompute_on_head_moved",
            item_key=item.key,
            head_revision=head_revision,
            components=affected,
        )
        await evaluate_project(twin, project_id, component_ids=affected, trigger="geometry_changed")
    except Exception as exc:  # noqa: BLE001 - never undo the head move
        logger.warning("dfm_recompute_failed", item_key=getattr(item, "key", None), error=str(exc))


async def on_context_written(twin: Any, project_id: str | UUID, outcome: Any) -> None:
    """After a manufacturing context write: recompute or revalidate. Never raises."""
    from twin_core.consistency.record_pins import set_record_status
    from twin_core.consistency.staleness import StalenessStatus

    stored = getattr(outcome, "stored", None)
    if stored is None or not getattr(outcome, "revision_created", False):
        return
    try:
        pid = _pid(project_id)
        nodes = await _sets(twin, pid)
        key = stored.item_key
        under = [
            n
            for n in nodes
            if (_valid(n) or _staleness(n) == "stale")
            and (
                key in _pinned_keys(n)
                or (
                    key == "MFC-PROJECT"
                    and str((n.metadata or {}).get("context_ref") or "") == "unresolved"
                )
            )
        ]
        if not under:
            return
        changed = set(getattr(outcome, "changed", []) or [])
        if changed & RELEVANT_CONTEXT_FIELDS:
            affected = sorted(
                {str(((n.metadata or {}).get(META_KEY) or {}).get("component_id")) for n in under}
            )
            _metric("record_dfm_stale_recompute", "context")
            logger.info(
                "dfm_recompute_on_context",
                context_ref=stored.ref,
                changed=sorted(changed),
                components=affected,
            )
            await evaluate_project(twin, pid, component_ids=affected, trigger="context_changed")
            return
        # Nothing the rules read changed: what this move marked stale still holds.
        words = ", ".join(sorted(changed)) or "nothing the rules read"
        for n in under:
            stale_for = (n.metadata or {}).get("stale_for") or {}
            if _staleness(n) == "stale" and set(stale_for) <= {key}:
                await set_record_status(
                    twin,
                    n.id,
                    StalenessStatus.REVALIDATED,
                    f"{stored.ref} changed {words}, which no DFM rule reads",
                )
        _metric("record_dfm_stale_recompute", "revalidated")
    except Exception as exc:  # noqa: BLE001 - the context write is saved
        logger.warning("dfm_context_recompute_failed", error=str(exc))


def register() -> None:
    """Listen for geometry head moves, after the listeners already registered."""
    from twin_core.consistency.record_pins import register_head_moved_listener

    register_head_moved_listener(on_head_moved)


# ---------------------------------------------------------------------------
# The view
# ---------------------------------------------------------------------------


class ComponentFindingsView(BaseModel):
    component_id: str
    name: str
    finding_set_id: str
    status: str
    status_label: str
    note: str
    process: str | None
    process_authority: str
    profile: dict[str, str] | None
    geometry_level: str
    geometry_refs: list[str]
    context_ref: str
    staleness: dict[str, Any]
    findings: list[dict[str, Any]]
    not_assessed: list[dict[str, Any]]
    rules_run: list[str]
    evaluated_at: str
    trigger: str = ""


class DfmView(BaseModel):
    project_id: str
    status: str
    status_label: str
    note: str
    #: Open findings (not kept by a person): the Create badge's number.
    suggestions: int
    blocking: int
    components: list[ComponentFindingsView] = Field(default_factory=list)
    #: Earlier sets kept for history (stale, superseded), newest first, when asked.
    history: list[ComponentFindingsView] = Field(default_factory=list)
    profiles: list[dict[str, str]] = Field(default_factory=list)
    heuristic_only: bool = True
    disclaimer: str = (
        "Preliminary manufacturing guidance from heuristic rules. Not an analysis pass, "
        "not a supplier confirmation and not a manufacturing release."
    )


def _component_view(node: Any, fs: DfmFindingSet) -> ComponentFindingsView:
    from api_gateway.twin.record_pins import record_view

    meta = node.metadata or {}
    view = record_view(RECORD_TYPE, meta)
    decisions = meta.get("dfm_decisions") or {}
    findings = []
    for f in fs.findings:
        row = f.model_dump(mode="json")
        if f.id in decisions:
            row["decision"] = decisions[f.id]
        findings.append(row)
    return ComponentFindingsView(
        component_id=fs.component_id,
        name=fs.component_name or fs.component_id,
        finding_set_id=str(node.id),
        status=fs.status.value,
        status_label=fs.status_label,
        note=fs.note,
        process=fs.process,
        process_authority=fs.process_authority,
        profile={"id": fs.profile_id, "version": str(fs.profile_version)}
        if fs.profile_id
        else None,
        geometry_level=fs.geometry_level,
        geometry_refs=list(fs.geometry_refs),
        context_ref=fs.context_ref,
        staleness=view.get("staleness") or {"status": _staleness(node)},
        findings=findings,
        not_assessed=[
            {"rule_id": n.rule_id, "title": n.title, "reason": n.reason, "text": n.text}
            for n in fs.not_assessed
        ],
        rules_run=list(fs.rules_run),
        evaluated_at=fs.evaluated_at,
        trigger=str(meta.get("trigger") or ""),
    )


async def dfm_view(twin: Any, project_id: str | UUID, *, include_history: bool = False) -> DfmView:
    """Current findings by component, the product status and the profile versions."""
    pid = _pid(project_id)
    with tracer.start_as_current_span("twin.dfm.view") as span:
        nodes = await _sets(twin, pid)
        current: dict[str, tuple[Any, DfmFindingSet]] = {}
        history: list[ComponentFindingsView] = []
        for node in nodes:
            fs = set_of(node)
            if fs is None:
                continue
            if _valid(node):
                current[fs.component_id] = (node, fs)
            elif include_history:
                history.append(_component_view(node, fs))
        components = [
            _component_view(node, fs)
            for node, fs in sorted(current.values(), key=lambda p: p[1].component_name.lower())
        ]
        statuses = [DfmStatus(c.status) for c in components]
        status = product_status(statuses)
        open_findings = [
            f
            for c in components
            for f in c.findings
            if not (
                isinstance(f.get("decision"), dict)
                and f["decision"].get("decision") == "keep_design"
            )
        ]
        blocking = sum(1 for f in open_findings if f.get("severity") == "blocking")
        profiles = sorted(
            {(c.profile["id"], c.profile["version"]) for c in components if c.profile}
        )
        notes = [c.note for c in components if c.status == status.value]
        span.set_attribute("dfm.components", len(components))
        return DfmView(
            project_id=str(pid),
            status=status.value,
            status_label=DFM_STATUS_LABELS[status],
            note=notes[0] if len(notes) == 1 else DFM_STATUS_LABELS[status],
            suggestions=len(open_findings),
            blocking=blocking,
            components=components,
            history=list(reversed(history)),
            profiles=[
                {"id": pid_, "version": ver, "process": PROFILES[pid_].process}
                for pid_, ver in profiles
                if pid_ in PROFILES
            ],
        )


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------


async def _find(twin: Any, pid: UUID, finding_id: str) -> tuple[Any, DfmFindingSet, Any]:
    for node in reversed(await _sets(twin, pid)):
        if not _valid(node):
            continue
        fs = set_of(node)
        if fs is None:
            continue
        for f in fs.findings:
            if f.id == finding_id:
                return node, fs, f
    raise DfmError(f"no current finding {finding_id} in this project", 404)


_GENERIC = {"the", "a", "of", "or", "and", "size", "length", "profile", "or_supplier"}


def _tokens(words: list[str]) -> set[str]:
    """Content words, singular ("walls" and "wall" match)."""
    out: set[str] = set()
    for w in words:
        for t in re.split(r"[^a-z0-9]+", str(w).lower()):
            if len(t) <= 2 or t in _GENERIC:
                continue
            out.add(t[:-1] if len(t) > 3 and t.endswith("s") and not t.endswith("ss") else t)
    return out


async def protected_conflicts(twin: Any, pid: UUID, finding: Any, component_name: str) -> list[str]:
    """Why applying ``finding``'s fix would trade away a protected requirement (DFM-015)."""
    from api_gateway.twin.manufacturing_context import effective_context

    reasons: list[str] = []
    try:
        stored = await effective_context(twin, pid, component_name or None)
    except Exception:  # noqa: BLE001 - an unreadable context protects nothing extra
        stored = None
    if stored is not None:
        for name in finding.fix_changes:
            if (
                name in CONTEXT_FIELDS
                and stored.record.get(name).authority is Authority.USER_STATED
            ):
                reasons.append(
                    f"the {name.replace('_', ' ')} was stated by a person ({stored.ref})"
                )
    tokens = _tokens([*finding.fix_changes, finding.anchor.feature or ""])
    try:
        from api_gateway.create.routes import get_create_store

        creation = get_create_store().get(str(pid))
        protected = [p for ref in creation.brief.reference_details for p in ref.protected_features]
    except Exception as exc:  # noqa: BLE001 - no Create record protects nothing
        logger.debug("dfm_protected_features_unavailable", error=str(exc))
        protected = []
    for feature in protected:
        if tokens & _tokens([feature]):
            reasons.append(f"it would change a protected feature: {feature}")
    return list(dict.fromkeys(reasons))


async def decide(
    twin: Any,
    project_id: str | UUID,
    finding_id: str,
    decision: DecisionKind,
    *,
    actor: str = "",
    reason: str = "",
    workflow: Any = None,
) -> dict[str, Any]:
    """Apply a person's decision on one finding. Nothing changes the design itself."""
    from twin_core.geometry_state import set_node_metadata

    pid = _pid(project_id)
    with tracer.start_as_current_span("twin.dfm.decide") as span:
        span.set_attribute("dfm.decision", decision)
        node, fs, finding = await _find(twin, pid, finding_id)
        now = datetime.now(UTC).isoformat()
        record = {"decision": decision, "by": actor, "at": now, "reason": reason[:500]}
        out: dict[str, Any] = {
            "finding_id": finding_id,
            "decision": decision,
            "finding_set_id": str(node.id),
            "component_id": fs.component_id,
            "applied": False,
        }
        try:
            if decision == "keep_design":
                decisions = dict((node.metadata or {}).get("dfm_decisions") or {})
                decisions[finding_id] = record
                await set_node_metadata(twin, node, dfm_decisions=decisions)
                out["message"] = (
                    "Kept the design as it is. The suggestion stays on record and is not "
                    "raised again for this part."
                )
            elif decision == "try_fix":
                conflicts = await protected_conflicts(twin, pid, finding, fs.component_name)
                proposal = {
                    "rule_id": finding.rule_id,
                    "profile": f"{finding.profile_id}@{finding.profile_version}",
                    "anchor": finding.anchor.model_dump(mode="json"),
                    "suggested_fix": finding.suggested_fix,
                    "changes": list(finding.fix_changes),
                    "measured": finding.measured,
                    "limit": finding.limit,
                    "unit": finding.unit,
                }
                out["proposal"] = proposal
                if conflicts:
                    if workflow is None:
                        from api_gateway.assistant.routes import workflow as default_workflow

                        workflow = default_workflow
                    change = await workflow.propose_change(
                        agent_code="dfm",
                        description=(
                            f"Manufacturing fix for {fs.component_name or fs.component_id}: "
                            f"{finding.suggested_fix} This trades away a protected requirement: "
                            + "; ".join(conflicts)
                        )[:2000],
                        diff={
                            "action": "record_decision",
                            "title": f"Apply DFM fix {finding.rule_id} to "
                            f"{fs.component_name or fs.component_id}",
                            "rationale": (
                                f"{finding.message} Suggested: {finding.suggested_fix} "
                                f"Protected: {'; '.join(conflicts)}"
                            )[:2000],
                            "dfm_finding": proposal,
                        },
                        work_products=[node.id],
                        project_id=str(pid),
                    )
                    out["requires_approval"] = True
                    out["approval_id"] = f"change:{change.change_id}"
                    out["protected"] = conflicts
                    out["message"] = (
                        "This fix would change something you protected, so it waits for "
                        "a person's approval."
                    )
                else:
                    out["requires_approval"] = False
                    out["message"] = (
                        "Here is the suggested fix as a proposal. Nothing is changed until "
                        "you apply it."
                    )
                decisions = dict((node.metadata or {}).get("dfm_decisions") or {})
                decisions[finding_id] = {**record, "requires_approval": bool(conflicts)}
                await set_node_metadata(twin, node, dfm_decisions=decisions)
            elif decision == "explore_process":
                out["alternatives"] = await explore_processes(twin, pid, fs.component_id)
                out["message"] = (
                    "Other ways to make this part, checked with the same preliminary rules. "
                    "Nothing was changed; choose a process to switch."
                )
            else:  # pragma: no cover - the route validates the literal
                raise DfmError(f"unknown decision {decision!r}", 422)
        except Exception:
            _metric("record_dfm_decision", decision, "failed")
            raise
        _metric("record_dfm_decision", decision, "recorded")
        logger.info(
            "dfm_decision_recorded",
            project_id=str(pid),
            finding_id=finding_id,
            decision=decision,
            requires_approval=out.get("requires_approval"),
        )
        return out


async def explore_processes(
    twin: Any, project_id: str | UUID, component_id: str
) -> list[dict[str, Any]]:
    """The component under every other profile. A preview: nothing is stored."""
    pid = _pid(project_id)
    gathered = [g for g in await gather_components(twin, pid) if g.component_id == component_id]
    if not gathered:
        raise DfmError(f"no component {component_id} in this project", 404)
    g = gathered[0]
    stored = await _context_for(twin, pid, g)
    ctx = process_context(stored)
    current = profile_for_process(ctx.process)
    out: list[dict[str, Any]] = []
    for profile in alternative_profiles(current):
        preview_ctx = ctx.model_copy(
            update={"process": profile.process, "process_authority": "explored"}
        )
        fs = evaluate(g.geometry, preview_ctx, profile=profile)
        out.append(
            {
                "process": profile.process,
                "profile": {"id": profile.id, "version": profile.version},
                "status": fs.status.value,
                "note": fs.note,
                "findings": len(fs.findings),
                "blocking": sum(1 for f in fs.findings if f.severity == "blocking"),
                "not_assessed": len(fs.not_assessed),
                "preview": True,
            }
        )
    return out


class DfmService:
    """What the MCP tools call: evaluate and read, bound to one twin."""

    def __init__(self, twin: Any) -> None:
        self._twin = twin

    async def evaluate(
        self, project_id: str, component_ids: list[str] | None = None
    ) -> dict[str, Any]:
        await evaluate_project(self._twin, project_id, component_ids=component_ids, trigger="tool")
        return (await dfm_view(self._twin, project_id)).model_dump(mode="json")

    async def findings(self, project_id: str, *, history: bool = False) -> dict[str, Any]:
        view = await dfm_view(self._twin, project_id, include_history=history)
        return view.model_dump(mode="json")


def make_dfm_service(twin: Any) -> DfmService:
    """The service ``twin.evaluate_dfm`` and ``twin.get_dfm_findings`` call."""
    return DfmService(twin)


# ---------------------------------------------------------------------------
# Generation guidance (DFM-003, DFM-005)
# ---------------------------------------------------------------------------


def manufacturability_variant(record: Any) -> tuple[VariantGuidance | None, str]:
    """The variant guidance when the process is specified; else ``(None, why not)``."""
    if record is None:
        return None, "no manufacturing context is recorded"
    process = record.selected_process
    if not process.known or process.stale:
        return None, "the process is not specified yet"
    profile = profile_for_process(str(process.value))
    if profile is None:
        return None, f"no rule profile covers {process.value}"
    material = record.selected_material
    return (
        variant_guidance(
            profile, material=str(material.value) if material.known and not material.stale else None
        ),
        "",
    )


__all__ = [
    "GEOMETRY_ITEM_TYPES",
    "META_KEY",
    "RECORD_TYPE",
    "RELEVANT_CONTEXT_FIELDS",
    "ComponentFindingsView",
    "DfmError",
    "DfmService",
    "DfmView",
    "Gathered",
    "decide",
    "dfm_view",
    "evaluate_project",
    "explore_processes",
    "gather_components",
    "make_dfm_service",
    "manufacturability_variant",
    "on_context_written",
    "on_head_moved",
    "process_context",
    "protected_conflicts",
    "register",
    "set_of",
]
