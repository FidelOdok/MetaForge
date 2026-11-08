"""The last steps from a validated design to a release, for an agent.

A headless agent that drove a product from a prompt to validated CAD, a board
and firmware still could not release it: it could not retire or map the
concept's illustrative components, and it could not find the mesh-then-solve
chain a structural check needs. This service backs two tools for that:

``twin.resolve_concept_component``
    Records how a concept component (``CMP-...``) is resolved: realised by the
    CAD items that make it, or retired with a reason, or restored. See
    :mod:`twin_core.component_resolution`; the maturity and the release
    checklist read the newest resolution.
``twin.run_item_fea``
    Runs a load case on a committed CAD revision by reference: reads its STEP
    from the twin, meshes it with ``freecad.generate_mesh``, picks the fixed
    and loaded surfaces (by name, or as the faces at the extreme of an axis),
    and solves with ``calculix.run_fea``. Adapter calls go over MCP through the
    exact CAD service's bridge, never directly.

Measuring DFM by reference is ``twin.measure_dfm`` (:mod:`api_gateway.twin.dfm_exact`).
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any
from uuid import UUID

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.release_path")

MESH_TOOL = "freecad.generate_mesh"
FEA_TOOL = "calculix.run_fea"
_STAGE_SUBDIR = "_staged_work_products"
#: Elements along the largest extent when no element size is given: coarse
#: enough to mesh a desk-sized part quickly, fine enough to be a first check.
_DEFAULT_ELEMENTS_ALONG = 40
_AXES = {"x": 0, "y": 1, "z": 2}


class ReleasePathError(ValueError):
    """A request that cannot be honoured; ``status`` is its HTTP status."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _metric(tool: str, outcome: str) -> None:
    try:
        from observability.metrics import collector_for

        collector_for("metaforge-gateway").record_release_path_call(tool, outcome)
    except Exception as exc:  # noqa: BLE001 - a counter never fails the call
        logger.debug("release_path_metric_failed", error=str(exc))


def _pid(project_id: str | UUID) -> UUID:
    try:
        return project_id if isinstance(project_id, UUID) else UUID(str(project_id))
    except ValueError as exc:
        raise ReleasePathError(f"project id {project_id!r} is not a UUID") from exc


# ---------------------------------------------------------------------------
# Face selection
# ---------------------------------------------------------------------------


def _bbox(face: dict[str, Any]) -> tuple[list[float], list[float]] | None:
    box = face.get("bbox_mm")
    if not isinstance(box, dict):
        return None
    lo, hi = box.get("min"), box.get("max")
    if not (isinstance(lo, list) and isinstance(hi, list) and len(lo) == 3 and len(hi) == 3):
        return None
    return [float(v) for v in lo], [float(v) for v in hi]


def select_surface(faces: list[dict[str, Any]], selector: Any, *, role: str) -> str:
    """The mesh surface set a selector names. Pure.

    ``selector`` is a set name (``"Surface3"``, as ``freecad.generate_mesh``
    lists them) or ``{"axis": "x"|"y"|"z", "side": "min"|"max"}``: every
    surface lying flat at that extreme of the part. When several surfaces lie
    there, the largest is used. Raises :class:`ReleasePathError` naming the
    surfaces that exist when nothing matches.
    """
    names = [str(f.get("name")) for f in faces if f.get("name")]
    if isinstance(selector, str):
        if selector in names:
            return selector
        raise ReleasePathError(
            f"{role}: no surface named {selector!r}; the mesh has {', '.join(names) or 'none'}"
        )
    if not isinstance(selector, dict):
        raise ReleasePathError(f"{role}: name a surface or give {{axis, side}}")
    axis = str(selector.get("axis") or "").lower()
    side = str(selector.get("side") or "").lower()
    if axis not in _AXES or side not in ("min", "max"):
        raise ReleasePathError(f"{role}: axis is x, y or z and side is min or max")
    i = _AXES[axis]
    boxes = [(f, _bbox(f)) for f in faces]
    known = [(f, b) for f, b in boxes if b is not None and f.get("name")]
    if not known:
        raise ReleasePathError(f"{role}: the mesh lists no surfaces with their positions")
    extreme = min(b[0][i] for _f, b in known) if side == "min" else max(b[1][i] for _f, b in known)
    span = max(b[1][i] for _f, b in known) - min(b[0][i] for _f, b in known)
    tol = max(1e-3, 1e-3 * span)
    flat = [
        f
        for f, b in known
        if abs(b[1][i] - b[0][i]) <= tol
        and abs((b[0][i] if side == "min" else b[1][i]) - extreme) <= tol
    ]
    if not flat:
        raise ReleasePathError(
            f"{role}: no flat surface lies at the {side} of {axis}; name one of "
            f"{', '.join(names)} instead"
        )
    best = max(flat, key=lambda f: float(f.get("area_mm2") or 0.0))
    return str(best["name"])


def default_element_size(meta: dict[str, Any]) -> float:
    """An element size from the revision's bounding box, else 2 mm. Pure."""
    box = meta.get("bbox_mm") or meta.get("bounding_box")
    extents: list[float] = []
    if isinstance(box, dict):
        for a in ("x", "y", "z"):
            lo, hi = box.get(f"min_{a}"), box.get(f"max_{a}")
            if isinstance(lo, int | float) and isinstance(hi, int | float):
                extents.append(abs(float(hi) - float(lo)))
        size = box.get("size") or box.get("extents")
        if not extents and isinstance(size, list):
            extents = [abs(float(v)) for v in size if isinstance(v, int | float)]
    elif isinstance(box, list):
        extents = [abs(float(v)) for v in box if isinstance(v, int | float)]
    largest = max(extents) if extents else 0.0
    if largest <= 0:
        return 2.0
    return round(max(0.5, largest / _DEFAULT_ELEMENTS_ALONG), 2)


# ---------------------------------------------------------------------------
# The service
# ---------------------------------------------------------------------------


class ReleasePathService:
    """What ``twin.resolve_concept_component`` and ``twin.run_item_fea`` call."""

    def __init__(
        self,
        twin: Any,
        *,
        cad_service: Any = None,
        workspace_dir: Path | None = None,
    ) -> None:
        self._twin = twin
        self._cad = cad_service
        self._workspace = workspace_dir

    def _cad_service(self) -> Any:
        if self._cad is not None:
            return self._cad
        from api_gateway.twin.exact_cad import get_exact_cad_service

        return get_exact_cad_service()

    def _root(self) -> Path:
        return self._workspace or Path(os.getenv("ADAPTER_WORKSPACE_DIR", "/workspace"))

    # -- concept components -------------------------------------------------

    async def resolve_concept_component(
        self,
        project_id: str,
        *,
        component_id: str,
        action: str,
        reason: str,
        item_keys: list[str] | None = None,
        actor: str = "",
        on_behalf_of: str = "",
    ) -> dict[str, Any]:
        """Record a resolution and return it with the checklist item it changes."""
        from twin_core.component_resolution import ComponentResolutionError, record_resolution
        from twin_core.dfm.release_checklist import manufacturing_checklist

        pid = _pid(project_id)
        with tracer.start_as_current_span("twin.resolve_concept_component") as span:
            span.set_attribute("component.id", component_id)
            span.set_attribute("resolution.action", action)
            try:
                out = await record_resolution(
                    self._twin,
                    pid,
                    component_id=component_id,
                    action=action,
                    reason=reason,
                    item_keys=item_keys,
                    actor=actor,
                    on_behalf_of=on_behalf_of,
                )
            except ComponentResolutionError as exc:
                span.record_exception(exc)
                _metric("twin.resolve_concept_component", "refused")
                raise ReleasePathError(str(exc), exc.status) from exc
            checklist = await manufacturing_checklist(self._twin, pid)
            if checklist is not None:
                item = checklist.item("components_exact")
                out["components_exact"] = {"status": item.status, "detail": item.detail}
            _metric("twin.resolve_concept_component", action)
            return out

    # -- the FEA chain ------------------------------------------------------

    async def _revision(self, pid: UUID, item_key: str | None, node_id: str | None) -> Any:
        from api_gateway.twin.dfm_exact import ExactDfmError, _target

        try:
            return await _target(self._twin, pid, node_id=node_id, item_key=item_key)
        except ExactDfmError as exc:
            if exc.status == 409:
                # Not validated: analyse it anyway, but say so in the result.
                return await self._any_revision(pid, item_key, node_id)
            raise ReleasePathError(str(exc), exc.status) from exc

    async def _any_revision(self, pid: UUID, item_key: str | None, node_id: str | None) -> Any:
        from twin_core.items import find_item, item_for_node, item_history, parse_item_ref

        if node_id:
            node = await self._twin.get_work_product(UUID(str(node_id)))
            found = await item_for_node(self._twin, UUID(str(node_id))) if node else None
            if node is None or found is None:
                raise ReleasePathError("no CAD revision to analyse", 404)
            item, revision = found
            return item, revision, node, "", item.name
        key, pin = parse_item_ref(str(item_key))
        head = await find_item(self._twin, key, pid)
        if head is None or head.head_node_id is None:
            raise ReleasePathError(f"{key} is not a CAD item of this project", 404)
        ref_node = head.head_node_id
        revision = pin or head.head_revision
        if pin and pin != head.head_revision:
            for rev in await item_history(self._twin, head, include_drafts=False):
                if rev.revision == pin:
                    ref_node = rev.node_id
        node = await self._twin.get_work_product(ref_node)
        if node is None:
            raise ReleasePathError(f"{key}@{revision} has no stored revision", 404)
        return head, revision, node, "", head.name

    def _stage(self, node: Any, key: str, step: bytes) -> str:
        folder = self._root() / _STAGE_SUBDIR / str(node.id)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{key}.step"
        if not path.exists() or path.stat().st_size != len(step):
            path.write_bytes(step)
        return str(path)

    async def run_item_fea(
        self,
        project_id: str,
        *,
        load_case: dict[str, Any],
        item_key: str | None = None,
        node_id: str | None = None,
        element_size: float | None = None,
        element_order: int = 1,
    ) -> dict[str, Any]:
        """Mesh a committed CAD revision and solve one load case on it."""
        if not item_key and not node_id:
            raise ReleasePathError("name the CAD revision: item_key (KEY or KEY@n) or node_id")
        if not isinstance(load_case, dict):
            raise ReleasePathError("load_case is an object")
        analysis = str(load_case.get("analysis_type") or "static_stress")
        if analysis not in ("static_stress", "modal"):
            raise ReleasePathError("load_case.analysis_type is static_stress or modal")
        material = load_case.get("material")
        if not isinstance(material, dict) or not material:
            raise ReleasePathError(
                "load_case.material is required: {name} from the material library, or "
                "{youngs_modulus_mpa, poissons_ratio}"
            )
        if load_case.get("fixed") is None:
            raise ReleasePathError("load_case.fixed names the surface held fixed")
        force = load_case.get("force_n")
        if analysis == "static_stress":
            if load_case.get("loaded") is None:
                raise ReleasePathError("load_case.loaded names the surface the force acts on")
            if not (
                isinstance(force, list)
                and len(force) == 3
                and all(isinstance(v, int | float) for v in force)
            ):
                raise ReleasePathError("load_case.force_n is [Fx, Fy, Fz] in newtons")
        if element_order not in (1, 2):
            raise ReleasePathError("element_order is 1 or 2")

        pid = _pid(project_id)
        started = time.monotonic()
        with tracer.start_as_current_span("twin.run_item_fea") as span:
            item, revision, node, _cid, _name = await self._revision(pid, item_key, node_id)
            ref = f"{item.key}@{revision}"
            span.set_attribute("item.ref", ref)
            span.set_attribute("fea.analysis_type", analysis)
            meta = dict(getattr(node, "metadata", None) or {})
            svc = self._cad_service()
            if svc is None:
                _metric("twin.run_item_fea", "unavailable")
                raise ReleasePathError("the CAD adapter is not available", 503)
            step = svc.read_step(node)
            if not step:
                _metric("twin.run_item_fea", "no_step")
                raise ReleasePathError(f"{ref} has no stored STEP to mesh", 409)
            input_file = self._stage(node, item.key, step)
            size = float(element_size) if element_size else default_element_size(meta)
            try:
                mesh = await svc.invoke_tool(
                    MESH_TOOL,
                    {
                        "input_file": input_file,
                        "element_size": size,
                        "output_format": "inp",
                        "element_order": element_order,
                    },
                )
            except Exception as exc:  # noqa: BLE001 - reported with the step that failed
                span.record_exception(exc)
                _metric("twin.run_item_fea", "mesh_failed")
                raise ReleasePathError(f"{ref}: meshing failed ({str(exc)[:300]})", 502) from exc
            faces = [f for f in mesh.get("faces") or [] if isinstance(f, dict)]
            fixed = select_surface(faces, load_case.get("fixed"), role="fixed")
            params: dict[str, Any] = {
                "mesh_file": mesh.get("mesh_file"),
                "load_case": str(load_case.get("name") or "load case")[:80],
                "analysis_type": analysis,
                "material": material,
                "fixed_node_set": fixed,
            }
            loaded: str | None = None
            if analysis == "static_stress":
                loaded = select_surface(faces, load_case.get("loaded"), role="loaded")
                if loaded == fixed:
                    raise ReleasePathError("the loaded surface is the fixed one; pick another")
                params["load_node_set"] = loaded
                params["load_force_n"] = [float(v) for v in force]  # type: ignore[union-attr]
            elif load_case.get("num_modes"):
                params["num_modes"] = int(load_case["num_modes"])
            try:
                result = await svc.invoke_tool(FEA_TOOL, params)
            except Exception as exc:  # noqa: BLE001
                span.record_exception(exc)
                _metric("twin.run_item_fea", "solve_failed")
                raise ReleasePathError(f"{ref}: the solve failed ({str(exc)[:300]})", 502) from exc
            state = str(meta.get("geometry_state") or "")
            _metric("twin.run_item_fea", "solved")
            logger.info(
                "item_fea_solved",
                item_ref=ref,
                analysis=analysis,
                elements=mesh.get("num_elements"),
                fixed=fixed,
                loaded=loaded,
                duration_s=round(time.monotonic() - started, 3),
            )
            return {
                "item_ref": ref,
                "node_id": str(node.id),
                "geometry_state": state or None,
                "validated": state in ("validated", "released"),
                "mesh": {
                    "mesh_file": mesh.get("mesh_file"),
                    "element_size": size,
                    "element_order": element_order,
                    "num_nodes": mesh.get("num_nodes"),
                    "num_elements": mesh.get("num_elements"),
                    "surfaces": [
                        {k: f.get(k) for k in ("name", "centroid_mm", "area_mm2", "normal")}
                        for f in faces
                    ],
                },
                "fixed_surface": fixed,
                "loaded_surface": loaded,
                "load_case": params["load_case"],
                "result": result,
                "warnings": list(result.get("warnings") or []),
                "record_with": {
                    "tool": "twin.record_document",
                    "document_type": "simulation_result",
                    "analysed_geometry_node_id": str(node.id),
                    "analysed_geometry_revision": revision,
                    "field_file": (result.get("field") or {}).get("file")
                    if isinstance(result.get("field"), dict)
                    else None,
                },
            }

    # -- the release approval, for a delegated agent (FORGE-834) --------------

    async def approve_release(
        self,
        project_id: str | UUID,
        *,
        agent: str,
        on_behalf_of: str,
        reason: str,
        entity_id: str | None = None,
    ) -> dict[str, Any]:
        """Approve the project's release approval as ``agent`` acting for ``on_behalf_of``.

        Reached only through ``twin.approve_release`` on a call the MCP
        dispatcher delegated under ``METAFORGE_ALLOW_AGENT_RELEASE``. The
        release rules stay: while any other checklist item the release gate
        reads is open, nothing is approved and each blocker is named. The
        entity's metadata says the agent approved it for the person; it never
        names the person as the approver.
        """
        from twin_core.dfm.release_checklist import manufacturing_checklist
        from twin_core.models.enums import AuthorityState

        tool = "twin.approve_release"
        pid = _pid(project_id)
        with tracer.start_as_current_span("release_path.approve_release") as span:
            span.set_attribute("project.id", str(pid))
            span.set_attribute("release.agent", agent)
            checklist = await manufacturing_checklist(self._twin, pid)
            blockers = checklist.gate_blockers() if checklist is not None else []
            if blockers:
                _metric(tool, "blocked")
                logger.info(
                    "release_approval_refused_blocked",
                    project_id=str(pid),
                    agent=agent,
                    on_behalf_of=on_behalf_of,
                    blockers=blockers,
                )
                raise ReleasePathError(
                    "the release cannot be approved while the release checklist has open "
                    "items: " + "; ".join(blockers),
                    409,
                )
            entities = await self._twin.list_engineering_entities(
                project_id=pid, entity_type="release_approval"
            )
            if entity_id is not None:
                target = next((e for e in entities if str(e.id) == entity_id), None)
                if target is None:
                    _metric(tool, "not_found")
                    raise ReleasePathError(f"no release_approval {entity_id} on this project", 404)
            else:
                pending = [
                    e
                    for e in entities
                    if str(getattr(e.authority, "value", e.authority))
                    not in ("approved", "baselined")
                ]
                if not pending:
                    _metric(tool, "not_found")
                    raise ReleasePathError(
                        "no release approval waiting: record one first with "
                        "twin.record_engineering_entity (entity_type release_approval)",
                        404,
                    )
                target = max(pending, key=lambda e: e.created_at)
            approved_by = f"agent:{agent}"
            metadata = dict(target.metadata or {})
            metadata.update(
                {
                    "approved_by": approved_by,
                    "agent": agent,
                    "on_behalf_of": on_behalf_of,
                    "approval_route": "delegated",
                    "approval_reason": reason,
                    "delegation": "METAFORGE_ALLOW_AGENT_RELEASE",
                }
            )
            updated = await self._twin.update_engineering_entity(
                target.id,
                {"authority": AuthorityState.APPROVED, "metadata": metadata},
                expected_revision=target.revision,
            )
            after = await manufacturing_checklist(self._twin, pid)
            item = after.item("approvals").as_dict() if after is not None else None
            _metric(tool, "ok")
            logger.info(
                "release_approval_delegated",
                project_id=str(pid),
                node_id=str(updated.id),
                agent=agent,
                on_behalf_of=on_behalf_of,
                revision=updated.revision,
            )
            return {
                "node_id": str(updated.id),
                "authority": updated.authority.value,
                "revision": updated.revision,
                "approved_by": approved_by,
                "on_behalf_of": on_behalf_of,
                "checklist_item": item,
            }


def make_release_path_service(twin: Any) -> ReleasePathService:
    """The service ``twin.resolve_concept_component`` and ``twin.run_item_fea`` call."""
    return ReleasePathService(twin)


__all__ = [
    "FEA_TOOL",
    "MESH_TOOL",
    "ReleasePathError",
    "ReleasePathService",
    "default_element_size",
    "make_release_path_service",
    "select_surface",
]
