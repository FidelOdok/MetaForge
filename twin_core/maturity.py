"""Geometry maturity per component, and a product readiness that never overclaims.

Section 9 of the C3D spec: maturity is tracked per component, not per
project. A product can have a validated enclosure and an illustrative handle
at the same time, and its readiness must say so (3D-RC-010).

=============  =================================================================
Illustrative   Only an appearance: named in a concept, shown in an AI mesh (L0)
               or a picture.
Structured     A part of a structured block mesh (L1) or a concept block layout:
               identified, with approximate sizes.
Parametric     Editable feature definitions: a CAD revision (its build source
               or script) that is not yet validated, or a parametric sketch
               bound to the component.
Exact          The CAD head is exact and passed the validity check
               (``geometry_state`` ``validated``).
Verified       Exact, and the applicable checks run against that head revision
               passed (the shared validation status: at least one ``PASS`` or
               ``WARN``, none ``FAIL`` or ``STALE``).
Released       The head revision was released through the release path.
=============  =================================================================

Accepting a visual proposal (a concept, a generated sketch, a 3D concept) is
recorded as acceptance and is never read here: nothing in this module looks at
an acceptance or approval flag, so acceptance can never stand in for
validation (SYNC-013).

Each component's maturity names its evidence (node ids and ``KEY@n`` refs),
so every status Create shows can be traced in Engineering (VIEW-006).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import UUID

import structlog

from observability.tracing import get_tracer
from twin_core.component_identity import project_identity
from twin_core.geometry_state import GeometryState, geometry_state_of
from twin_core.representation import (
    COMPONENT_ID_KEY,
    PART_COMPONENT_IDS_KEY,
    STRUCTURED_MESH_TOOL,
    representation_of,
)

logger = structlog.get_logger(__name__)
tracer = get_tracer("twin_core.maturity")


class Maturity(StrEnum):
    ILLUSTRATIVE = "Illustrative"
    STRUCTURED = "Structured"
    PARAMETRIC = "Parametric"
    EXACT = "Exact"
    VERIFIED = "Verified"
    RELEASED = "Released"


ORDER: tuple[Maturity, ...] = tuple(Maturity)


def rank(m: Maturity | str) -> int:
    return ORDER.index(Maturity(m))


@dataclass
class ComponentMaturity:
    component_id: str
    name: str
    required: bool
    source: str = "made"
    maturity: Maturity = Maturity.ILLUSTRATIVE
    #: ``[{maturity, kind, node_id, ref, detail}]``, one per claim reached.
    evidence: list[dict[str, Any]] = field(default_factory=list)
    #: What the next level needs, in plain words.
    next_step: str | None = None

    def claim(
        self, level: Maturity, kind: str, *, node_id: Any = None, ref: Any = None, detail: str = ""
    ) -> None:
        self.evidence.append(
            {
                "maturity": level.value,
                "kind": kind,
                "node_id": str(node_id) if node_id else None,
                "ref": str(ref) if ref else None,
                "detail": detail,
            }
        )
        if rank(level) > rank(self.maturity):
            self.maturity = level

    def as_dict(self) -> dict[str, Any]:
        return {
            "component_id": self.component_id,
            "name": self.name,
            "required": self.required,
            "source": self.source,
            "maturity": self.maturity.value,
            "evidence": self.evidence,
            "next_step": self.next_step,
        }


_NEXT: dict[Maturity, str] = {
    Maturity.ILLUSTRATIVE: "show it as simple 3D parts or build its CAD",
    Maturity.STRUCTURED: "build its CAD",
    Maturity.PARAMETRIC: "build and validate its exact model",
    Maturity.EXACT: "run the checks that apply to it",
    Maturity.VERIFIED: "release the revision",
}


def _wp_type(node: Any) -> str:
    t = getattr(node, "type", None)
    return str(getattr(t, "value", t) or "")


def aggregate(components: list[ComponentMaturity]) -> dict[str, Any]:
    """Product readiness from its components; never above its least mature required one."""
    by: dict[str, list[str]] = {m.value: [] for m in ORDER}
    for c in components:
        by[c.maturity.value].append(c.component_id)
    required = [c for c in components if c.required]
    pool = required or components
    if not pool:
        return {
            "maturity": None,
            "by_maturity": by,
            "required_count": 0,
            "component_count": 0,
            "limited_by": [],
            "claims": {"exact": False, "verified": False, "released": False},
            "summary": "No components are known yet: record a concept assembly or build CAD.",
        }
    floor = min(pool, key=lambda c: rank(c.maturity)).maturity
    limited = [c.component_id for c in pool if c.maturity is floor]
    claims = {
        "exact": all(rank(c.maturity) >= rank(Maturity.EXACT) for c in pool),
        "verified": all(rank(c.maturity) >= rank(Maturity.VERIFIED) for c in pool),
        "released": all(rank(c.maturity) >= rank(Maturity.RELEASED) for c in pool),
    }
    counts = [f"{len(ids)} {m.lower()}" for m, ids in by.items() if ids]
    scope = "required" if required else "known"
    summary = (
        f"{len(pool)} {scope} component(s): "
        + ", ".join(counts)
        + f". The product is {floor.value}"
        + ("" if len(limited) == len(pool) else f", limited by {', '.join(limited)}")
        + "."
    )
    return {
        "maturity": floor.value,
        "by_maturity": by,
        "required_count": len(required),
        "component_count": len(components),
        "limited_by": limited,
        "claims": claims,
        "summary": summary,
    }


async def _check_results(twin: Any, pid: UUID) -> dict[str, list[dict[str, Any]]]:
    """``{KEY@n: [check result dicts]}`` for every current record pinned to a revision."""
    from twin_core.consistency.record_pins import record_pins
    from twin_core.consistency.validation_status import check_result_for_record
    from twin_core.items.current import project_records

    out: dict[str, list[dict[str, Any]]] = {}
    try:
        records = await project_records(twin, pid)
    except Exception as exc:  # noqa: BLE001 - no records reads as no checks
        logger.warning("maturity_records_unavailable", error=str(exc))
        return out
    for rec in records:
        if _wp_type(rec) == "design_decision":
            continue  # a decision is a choice, not a check
        meta = dict(getattr(rec, "metadata", None) or {})
        result = check_result_for_record(
            str(getattr(rec, "id", "")),
            meta,
            check=str(getattr(rec, "name", "") or _wp_type(rec) or "check"),
            tool=str(meta.get("tool") or meta.get("source_tool") or "") or None,
        )
        for pin in record_pins(meta):
            ref = pin.get("item_ref") or (
                f"{pin.get('item_key')}@{pin.get('revision')}" if pin.get("revision") else None
            )
            if ref:
                out.setdefault(str(ref), []).append(result.model_dump(mode="json"))
    return out


async def compute_maturity(twin: Any, project_id: UUID | str) -> dict[str, Any]:
    """Per-component maturity and the product's aggregate readiness, with evidence."""
    from twin_core.items import list_items, supports_items
    from twin_core.models.enums import WorkProductType

    pid = project_id if isinstance(project_id, UUID) else UUID(str(project_id))
    with tracer.start_as_current_span("twin.maturity.compute") as span:
        span.set_attribute("project.id", str(pid))
        ident = await project_identity(twin, pid)
        comps: dict[str, ComponentMaturity] = {}
        has_assembly = bool(ident["components"])
        for cid, info in ident["components"].items():
            comp = ComponentMaturity(
                component_id=cid,
                name=str(info["name"]),
                required=bool(info.get("in_current")) and info.get("source") == "made",
                source=str(info.get("source") or "made"),
            )
            comp.claim(
                Maturity.ILLUSTRATIVE,
                "concept_assembly",
                node_id=info.get("decision_node_id"),
                detail="named in the concept's selected assembly",
            )
            comps[cid] = comp

        def ensure(cid: str, name: str) -> ComponentMaturity:
            if cid not in comps:
                comps[cid] = ComponentMaturity(
                    component_id=cid, name=name, required=not has_assembly
                )
            return comps[cid]

        renders: list[Any] = []
        for wp_type in (WorkProductType.RENDER, WorkProductType.IMAGE):
            try:
                renders += await twin.list_work_products(work_product_type=wp_type, project_id=pid)
            except Exception as exc:  # noqa: BLE001
                logger.warning("maturity_renders_unavailable", error=str(exc))
        for wp in renders:
            meta = dict(getattr(wp, "metadata", None) or {})
            rep = representation_of(_wp_type(wp), meta, fmt=str(getattr(wp, "format", "") or ""))
            if rep is None:
                continue
            if meta.get("generated_by") == STRUCTURED_MESH_TOOL:
                members = meta.get(PART_COMPONENT_IDS_KEY) or {}
                for name, cid in members.items() if isinstance(members, dict) else []:
                    ensure(str(cid), str(name)).claim(
                        Maturity.STRUCTURED,
                        "structured_mesh",
                        node_id=wp.id,
                        ref=rep.get("source_revision"),
                        detail="a part of the structured 3D mesh (L1), sizes estimated",
                    )
            else:
                for comp in comps.values():
                    comp.claim(
                        Maturity.ILLUSTRATIVE,
                        "concept_" + ("mesh" if rep["level"] == "L0" else "image"),
                        node_id=wp.id,
                        detail=f"shown in an illustrative {rep['level']} artifact",
                    )

        checks = await _check_results(twin, pid)
        if supports_items(twin):
            for item_type in ("cad_model", "assembly"):
                for item in await list_items(twin, project_id=pid, item_type=item_type):
                    if item.head_node_id is None:
                        continue
                    node = await twin.graph.get_node(item.head_node_id)
                    if node is None:
                        continue
                    meta = dict(getattr(node, "metadata", None) or {})
                    if str(meta.get("model_role") or "") == "analysis":
                        continue
                    ref = f"{item.key}@{item.head_revision}"
                    targets: list[tuple[str, str]] = []
                    if meta.get(COMPONENT_ID_KEY):
                        targets.append((str(meta[COMPONENT_ID_KEY]), item.name))
                    members = meta.get(PART_COMPONENT_IDS_KEY)
                    if isinstance(members, dict):
                        targets += [(str(c), str(n)) for n, c in members.items()]
                    if not targets:
                        targets.append((f"item:{item.key}", item.name))
                    state = geometry_state_of("cad_model", meta) or GeometryState.EXACT_READY
                    results = checks.get(ref, [])
                    for cid, name in targets:
                        _claim_cad(ensure(cid, name), item.head_node_id, ref, state, results)

        for wp_type in (WorkProductType.PARAMETRIC_SKETCH, WorkProductType.SKETCH_INTERPRETATION):
            try:
                sketches = await twin.list_work_products(work_product_type=wp_type, project_id=pid)
            except Exception:  # noqa: BLE001
                sketches = []
            for wp in sketches:
                cid = (getattr(wp, "metadata", None) or {}).get(COMPONENT_ID_KEY)
                if cid and cid in comps:
                    comps[str(cid)].claim(
                        Maturity.PARAMETRIC,
                        "parametric_sketch",
                        node_id=wp.id,
                        detail="editable sketch with dimensions and constraints",
                    )

        out = sorted(comps.values(), key=lambda c: (not c.required, c.name.lower()))
        for c in out:
            c.next_step = _NEXT.get(c.maturity)
        agg = aggregate(out)
        span.set_attribute("maturity.components", len(out))
        span.set_attribute("maturity.product", str(agg["maturity"]))
        logger.info(
            "maturity_computed",
            project_id=str(pid),
            components=len(out),
            product=agg["maturity"],
            by_maturity={k: len(v) for k, v in agg["by_maturity"].items() if v},
        )
        return {
            "project_id": str(pid),
            "components": [c.as_dict() for c in out],
            "product": agg,
            "has_assembly": has_assembly,
        }


def _claim_cad(
    comp: ComponentMaturity,
    node_id: Any,
    ref: str,
    state: GeometryState,
    results: list[dict[str, Any]],
) -> None:
    comp.claim(
        Maturity.PARAMETRIC,
        "cad_revision",
        node_id=node_id,
        ref=ref,
        detail=f"CAD revision with feature definitions, geometry {state.value}",
    )
    if state not in (GeometryState.VALIDATED, GeometryState.RELEASED):
        return
    comp.claim(
        Maturity.EXACT,
        "geometry_validity",
        node_id=node_id,
        ref=ref,
        detail="exact B-Rep passed the validity check",
    )
    statuses = [str(r.get("status")) for r in results]
    passing = [r for r in results if r.get("status") in ("PASS", "WARN")]
    blocked = any(s in ("FAIL", "STALE") for s in statuses)
    if passing and not blocked:
        for r in passing:
            comp.claim(
                Maturity.VERIFIED,
                "check",
                node_id=r.get("tool_result_ref"),
                ref=ref,
                detail=f"{r.get('check')}: {r.get('status')}",
            )
    if state is GeometryState.RELEASED and comp.maturity is Maturity.VERIFIED:
        comp.claim(Maturity.RELEASED, "release", node_id=node_id, ref=ref, detail="released")
    elif state is GeometryState.RELEASED:
        comp.evidence.append(
            {
                "maturity": Maturity.RELEASED.value,
                "kind": "release",
                "node_id": str(node_id),
                "ref": ref,
                "detail": "released, but no passing check is recorded against it",
            }
        )


__all__ = [
    "ORDER",
    "ComponentMaturity",
    "Maturity",
    "aggregate",
    "compute_maturity",
    "rank",
]
