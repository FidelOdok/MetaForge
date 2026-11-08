"""Release package: a versioned snapshot of a project's hierarchy, BOM,
evidence, and decisions (FORGE-299, gap G-I3).

**What this is.** `twin.create_release_package` bundles the current state
of four already-real data sources -- the hierarchy tree
(`twin.list_hierarchy_nodes`), the BOM (`twin.list_bom_items`), Evidence
entities (`twin.list_engineering_entities(entity_type="evidence")`), and
Decision records (`twin.list_work_products(work_product_type=
WorkProductType.DESIGN_DECISION)`) -- into one new `release_package`
EngineeringEntity (FORGE-45's generic node), recorded via the existing
`engineering_entity_recorder` closure every other entity type already goes
through. The snapshot holds ONLY the ids of the items it references, never
a deep copy of their content -- a release package is a pointer-in-time
index, not a duplicate archive.

**The real gate this ticket reuses, not duplicates.** `release_approval`
(FORGE-73) and G8's `evaluate_g8_release` (`twin_core.consistency.gates`)
already existed before this ticket, real and comprehensive (baseline fixed,
no stale evidence, verification coverage complete, waivers approved, a
`release_approval` entity approved) -- but purely *informational*: nothing
in this codebase had ever called it as anything but an advisory display
value (`api_gateway/runs/gate_eval.py`'s `TwinConsistencyGateChecker`, which
itself never blocks a gate transition on the result). This module gives G8
its first real *consumer with teeth*: `create_release_package` calls
`evaluate_g8_release` itself and refuses to create a package unless it
returns `GateStatus.PASSED`. This is additive -- it does not change
`gate_eval.py`'s non-blocking posture anywhere else in the system; G8
remains purely advisory everywhere except this one new creation-time check.

**"Diff against previous release."** Deliberately a simple, real count
delta (hierarchy/BOM/evidence/decision counts, current minus the
immediately-prior package for the same project) -- not a structural diff.
No diff/comparison utility exists anywhere in `twin_core` to build a real
structural diff on top of, and inventing one is out of scope for this
ticket.

**Deliberately out of scope**:
- **Drawings.** FORGE-293 ("2D technical drawings... GD&T") has not
  shipped -- zero drawing-generation capability exists anywhere in this
  codebase (confirmed during FORGE-294's own scoping investigation, which
  hit the identical gap for CNC manufacturing outputs). A release package's
  snapshot carries an always-empty `drawing_ids` list rather than fake
  drawing content or a silently-missing field.
- **A full structural diff** between two releases' hierarchy/BOM/evidence
  content -- the count-delta described above ships instead.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog

from observability.tracing import get_tracer
from twin_core.consistency.gates import GateStatus, evaluate_g8_release
from twin_core.models.enums import WorkProductType

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.release_package")

_SNAPSHOT_LIST_KEYS = (
    "hierarchy_node_ids",
    "bom_item_ids",
    "evidence_ids",
    "decision_ids",
    "drawing_ids",
)


#: The work product types a release package's file manifest lists: what a
#: maker needs (CAD, board fabrication and assembly files, firmware, slicer
#: output, BOM) and nothing that only illustrates.
MANIFEST_TYPES: tuple[WorkProductType, ...] = (
    WorkProductType.CAD_MODEL,
    WorkProductType.SCHEMATIC,
    WorkProductType.PCB_LAYOUT,
    WorkProductType.GERBER,
    WorkProductType.PICK_AND_PLACE,
    WorkProductType.MANUFACTURING_FILE,
    WorkProductType.BOM,
    WorkProductType.FIRMWARE_SOURCE,
    WorkProductType.PINMAP,
    WorkProductType.GCODE,
)


def _created_key(node: Any) -> str:
    stamp = getattr(node, "created_at", None)
    return stamp.isoformat() if stamp is not None else ""


async def design_file_manifest(twin: Any, project_id: UUID) -> list[dict[str, Any]]:
    """The current design files a release pins, one row per file. Never raises.

    Item-tracked files (CAD parts and assemblies) are their head revision,
    ``KEY@n``; an analysis model is left out. Other files are the newest of
    each type and name. Each row carries the node id, format and content hash,
    so the package pins exactly the bytes it was cut from, and its
    ``source_ref``: the revision it was made from (a design file is its own).
    A fabrication output carries ``source_status`` too (``current``,
    ``stale``, ``unpinned`` or ``not_required``); one made again from the
    current revision supersedes the older ones and they are left out.
    """
    from twin_core.dfm.release_fit import OUTPUT_KINDS, output_sources
    from twin_core.items import item_for_node, list_items, supports_items

    rows: list[dict[str, Any]] = []
    heads: set[str] = set()
    if supports_items(twin):
        for item_type in ("cad_model", "assembly"):
            try:
                items = await list_items(twin, project_id=project_id, item_type=item_type)
            except Exception as exc:  # noqa: BLE001 - a manifest lists what it can read
                logger.warning("release_manifest_items_unavailable", error=str(exc))
                items = []
            for item in items:
                if item.head_node_id is None:
                    continue
                node = await twin.get_work_product(item.head_node_id)
                if node is None:
                    continue
                meta = dict(getattr(node, "metadata", None) or {})
                if str(meta.get("model_role") or "") == "analysis":
                    continue
                heads.add(str(node.id))
                rows.append(
                    {
                        "kind": item_type,
                        "ref": f"{item.key}@{item.head_revision}",
                        "name": item.name,
                        "node_id": str(node.id),
                        "format": str(getattr(node, "format", "") or ""),
                        "content_hash": str(getattr(node, "content_hash", "") or ""),
                        "geometry_state": meta.get("geometry_state"),
                        "source_ref": f"{item.key}@{item.head_revision}",
                    }
                )
    try:
        outputs = await output_sources(twin, project_id)
    except Exception as exc:  # noqa: BLE001 - a manifest lists what it can read
        logger.warning("release_manifest_outputs_unavailable", error=str(exc))
        outputs = []
    for out in outputs:
        if out.status == "superseded":
            continue
        rows.append(
            {
                "kind": out.kind,
                "ref": out.ref,
                "name": out.name,
                "node_id": out.node_id,
                "format": out.format,
                "content_hash": out.content_hash,
                "source_ref": out.source_ref,
                "source_status": out.status,
            }
        )
    for wp_type in MANIFEST_TYPES:
        if wp_type.value in OUTPUT_KINDS:
            continue  # listed with their sources above
        try:
            nodes = await twin.list_work_products(project_id=project_id, work_product_type=wp_type)
        except Exception as exc:  # noqa: BLE001
            logger.warning("release_manifest_type_unavailable", type=wp_type.value, error=str(exc))
            continue
        newest: dict[str, Any] = {}
        for node in sorted(nodes, key=_created_key):
            if str(node.id) in heads or wp_type is WorkProductType.CAD_MODEL:
                continue  # CAD is listed by item head above, never a superseded revision
            newest[str(getattr(node, "name", "") or node.id)] = node
        for name, node in sorted(newest.items()):
            own = None
            if supports_items(twin):
                try:
                    found = await item_for_node(twin, node.id)
                except Exception as exc:  # noqa: BLE001 - an unknown item pins nothing
                    logger.debug("release_manifest_item_lookup_failed", error=str(exc))
                    found = None
                if found is not None:
                    if found[0].head_node_id not in (None, node.id):
                        continue  # an older revision of a design file
                    own = f"{found[0].key}@{found[1]}"
            rows.append(
                {
                    "kind": wp_type.value,
                    "ref": own,
                    "name": name,
                    "node_id": str(node.id),
                    "format": str(getattr(node, "format", "") or ""),
                    "content_hash": str(getattr(node, "content_hash", "") or ""),
                    # A design file (schematic, board, firmware, pin map) is
                    # its own source: its build outputs live in its revision.
                    "source_ref": own,
                }
            )
    return rows


def stale_outputs(rows: list[dict[str, Any]]) -> list[str]:
    """One line per fabrication output in a manifest not made from the current revision."""
    out = []
    for r in rows:
        status = r.get("source_status")
        label = r.get("ref") or r.get("name")
        if status == "stale":
            out.append(
                f"{label} ({r['kind']}) was made from {r.get('source_ref')}, "
                "not the current revision"
            )
        elif status == "unpinned":
            out.append(f"{label} ({r['kind']}) does not say which revision it was made from")
    return out


def manifest_digest(rows: list[dict[str, Any]]) -> str:
    """One sha256 over the manifest rows, independent of their order. Pure."""
    import hashlib
    import json

    lines = sorted(
        json.dumps(
            {k: r.get(k) for k in ("kind", "ref", "name", "node_id", "content_hash", "source_ref")},
            sort_keys=True,
        )
        for r in rows
    )
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


async def _snapshot(twin: Any, project_id: UUID) -> dict[str, Any]:
    hierarchy_nodes = await twin.list_hierarchy_nodes(project_id=project_id)
    bom_items = await twin.list_bom_items(project_id=project_id)
    evidence = await twin.list_engineering_entities(project_id=project_id, entity_type="evidence")
    decisions = await twin.list_work_products(
        project_id=project_id, work_product_type=WorkProductType.DESIGN_DECISION
    )
    files = await design_file_manifest(twin, project_id)
    return {
        "hierarchy_node_ids": [str(n.id) for n in hierarchy_nodes],
        "bom_item_ids": [str(i.id) for i in bom_items],
        "evidence_ids": [str(e.id) for e in evidence],
        "decision_ids": [str(d.id) for d in decisions],
        # FORGE-293 (2D technical drawings) hasn't shipped -- always empty,
        # never fabricated, until that capability exists to snapshot.
        "drawing_ids": [],
        # The design files a maker needs, pinned by revision and content hash.
        "design_files": files,
        "manifest_sha256": manifest_digest(files),
    }


def _count_diff(prior: dict[str, Any] | None, current: dict[str, Any]) -> dict[str, Any]:
    if prior is None:
        return {
            "compared_to": None,
            "hierarchy_delta": len(current["hierarchy_node_ids"]),
            "bom_delta": len(current["bom_item_ids"]),
            "evidence_delta": len(current["evidence_ids"]),
            "decision_delta": len(current["decision_ids"]),
        }
    return {
        "compared_to": prior["node_id"],
        "hierarchy_delta": len(current["hierarchy_node_ids"]) - len(prior["hierarchy_node_ids"]),
        "bom_delta": len(current["bom_item_ids"]) - len(prior["bom_item_ids"]),
        "evidence_delta": len(current["evidence_ids"]) - len(prior["evidence_ids"]),
        "decision_delta": len(current["decision_ids"]) - len(prior["decision_ids"]),
    }


def _record_refusal(gate: Any) -> None:
    """Count a refused release by what blocked it first (C3D slice 6). Never raises."""
    blocker = "gate"
    for check in gate.checks:
        if check.status.value == "pass":
            continue
        if check.id == "manufacturing_release_checklist":
            blocker = "manufacturing_checklist"
        elif check.id == "exact_cad_validated":
            blocker = "step_round_trip"
        elif check.id == "release_approved":
            blocker = "approvals"
        break
    _record_blocker(blocker)


def _record_blocker(blocker: str) -> None:
    try:
        from observability.metrics import collector_for

        collector_for("metaforge-gateway").record_manufacturing_release_refused(blocker)
    except Exception as exc:  # noqa: BLE001 - a counter never changes the refusal
        logger.debug("release_refusal_metric_failed", error=str(exc))
    logger.info("manufacturing_release_refused", blocker=blocker)


async def _release_cad_heads(twin: Any, project_id: UUID) -> list[str]:
    """Mark the project's validated CAD heads ``released``. Never raises."""
    from twin_core.geometry_state import exact_cad_readiness, mark_released

    released: list[str] = []
    try:
        for entry in await exact_cad_readiness(twin, project_id):
            if not entry.ready:
                continue
            node = await twin.graph.get_node(UUID(entry.node_id))
            if node is not None and await mark_released(twin, node):
                released.append(f"{entry.item_key}@{entry.head_revision}")
    except Exception as exc:  # noqa: BLE001 - the package itself is recorded
        logger.warning("release_cad_marking_failed", project_id=str(project_id), error=str(exc))
    return released


def make_release_package_creator(
    twin: Any,
    *,
    engineering_entity_recorder: Any,
    traceability_coverage: Any = None,
) -> Any:
    """Return an async ``create(*, project_id, notes=None) -> dict`` bound
    to a twin + engineering_entity_recorder (+ optional
    ``traceability_coverage`` accessor, a ``Callable[[UUID], Awaitable]``
    matching ``twin_core.consistency.gates.TraceabilityCoverageAccessor``,
    so G8's 'verification complete' check can actually evaluate instead of
    reporting NOT_EVALUATED)."""

    async def create(*, project_id: str, notes: str | None = None) -> dict[str, Any]:
        with tracer.start_as_current_span("twin.create_release_package") as span:
            pid = UUID(project_id)
            span.set_attribute("release.project_id", project_id)

            gate = await evaluate_g8_release(twin, pid, traceability_coverage=traceability_coverage)
            span.set_attribute("release.gate_status", gate.status.value)
            if gate.status != GateStatus.PASSED:
                _record_refusal(gate)
                failing = "; ".join(
                    f"{c.label}: {c.detail}" for c in gate.checks if c.status.value != "pass"
                )
                raise ValueError(
                    "twin.create_release_package: G8 release gate is "
                    f"{gate.status.value}, not passed -- {failing}"
                )

            snapshot = await _snapshot(twin, pid)
            # Every fabrication output must be made from the released revision,
            # whether or not the manufacturing checklist applies to the project.
            stale = stale_outputs(snapshot["design_files"])
            if stale:
                _record_blocker("outputs_current")
                raise ValueError(
                    "twin.create_release_package: fabrication outputs are not made from the "
                    "released revisions -- " + "; ".join(stale) + ". Make them again from the "
                    "current revision and record each with twin.record_design_files (source_ref)."
                )

            existing = await twin.list_engineering_entities(
                project_id=pid, entity_type="release_package"
            )
            prior: dict[str, Any] | None = None
            if existing:
                prior_entity = max(existing, key=lambda e: e.created_at)
                prior = {
                    "node_id": str(prior_entity.id),
                    **{k: prior_entity.metadata.get(k, []) for k in _SNAPSHOT_LIST_KEYS},
                }

            diff = _count_diff(prior, snapshot)

            statement = (
                f"Release package: {len(snapshot['design_files'])} design files, "
                f"{len(snapshot['hierarchy_node_ids'])} hierarchy nodes, "
                f"{len(snapshot['bom_item_ids'])} BOM items, "
                f"{len(snapshot['evidence_ids'])} evidence, "
                f"{len(snapshot['decision_ids'])} decisions"
            )
            created_at = datetime.now(UTC)
            title = notes or f"Release package {created_at.isoformat()}"

            recorded = await engineering_entity_recorder(
                entity_type="release_package",
                statement=statement,
                title=title,
                extra={
                    **snapshot,
                    "diff_from_previous": diff,
                    "gate_status": gate.status.value,
                },
                project_id=project_id,
            )

            # CDE slice 6: the release path is the only one that marks CAD
            # revisions released. G8 passed, so each CAD head is validated
            # with a round-tripped STEP of that very revision.
            released = await _release_cad_heads(twin, pid)

            logger.info(
                "release_package_created",
                project_id=project_id,
                released_cad=released,
                node_id=recorded["node_id"],
                hierarchy_count=len(snapshot["hierarchy_node_ids"]),
                bom_count=len(snapshot["bom_item_ids"]),
                evidence_count=len(snapshot["evidence_ids"]),
                decision_count=len(snapshot["decision_ids"]),
            )

            return {
                **recorded,
                "title": title,
                "statement": statement,
                "created_at": created_at.isoformat(),
                "snapshot": snapshot,
                "diff_from_previous": diff,
                "gate_status": gate.status.value,
            }

    return create


def make_release_package_lister(twin: Any) -> Any:
    """Return an async ``list_packages(*, project_id) -> list[dict]``,
    oldest first, each carrying its own already-computed
    ``diff_from_previous`` (computed once at creation time, not recomputed
    on every list call)."""

    async def list_packages(*, project_id: str) -> list[dict[str, Any]]:
        pid = UUID(project_id)
        entities = await twin.list_engineering_entities(
            project_id=pid, entity_type="release_package"
        )
        entities_sorted = sorted(entities, key=lambda e: e.created_at)
        return [
            {
                "node_id": str(e.id),
                "created_at": e.created_at.isoformat(),
                "title": e.title,
                "statement": e.statement,
                "snapshot": {
                    **{k: e.metadata.get(k, []) for k in _SNAPSHOT_LIST_KEYS},
                    "design_files": e.metadata.get("design_files", []),
                    "manifest_sha256": e.metadata.get("manifest_sha256"),
                },
                "diff_from_previous": e.metadata.get("diff_from_previous"),
                "gate_status": e.metadata.get("gate_status"),
            }
            for e in entities_sorted
        ]

    return list_packages
