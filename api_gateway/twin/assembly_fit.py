"""Design for assembly: build an assembly of the released revisions and check it fits.

An autonomous run released a desk lamp whose parts could not be put together:
the board's mounting holes missed the cover's, the washers could not fit the
hinge gaps, the diffuser was larger than its seat, the USB-C socket sat inside
the board edge. Each part was valid on its own; nothing ever placed them
together. This service backs three tools that close that gap:

``twin.build_assembly``
    Places each part revision (a committed ``cad_model``, ``CAD-...@n``) at a
    location, with a board as the STEP ``kicad.export_3d`` made of it
    (committed with ``twin.commit_geometry``; it records which ``PCB-...@n``
    it is), builds the assembly STEP with ``cadquery.create_assembly`` and
    commits it as the next revision of an ``assembly`` item. The revision
    keeps the layout: each body's ``item_ref``, ``board_ref``, location and
    placement matrix, and the joints (``revolute`` with an axis and limits).
``twin.check_assembly``
    Checks an assembly revision (:mod:`twin_core.dfa.checks`): the bodies are
    the current revisions, no two overlap, stated gaps, hole patterns,
    outlines in pockets, edge connectors on their slots, fastener and washer
    stacks, each revolute joint swept through its limits, seated parts. The
    solids are measured by ``cadquery.check_interference`` over MCP. Hole
    patterns and the board outline come from the ``IFC-CAD-EDA`` contract and
    the parts' metadata when the call does not give them. The result is an
    ``assembly_check`` record pinned to the assembly and every part revision.
``twin.accept_release_finding``
    A person, or an agent the owner delegates release to acting for a person,
    accepts a failing ERC/DRC or an assembly finding: an approved ``waiver``
    with the reason, pinned to the revisions it is about.

The release checklist's "Assembly checked (DFA)" and "ERC and DRC pass" items
read these records (:mod:`twin_core.dfm.release_fit`).
"""

from __future__ import annotations

import base64
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from uuid import UUID

import structlog

from observability.tracing import get_tracer
from twin_core.dfa.geometry import (
    Body,
    EngineResult,
    Matrix,
    Overlap,
    Pose,
    flat,
    location_matrix,
    matrix_from,
)

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.assembly_fit")

ASSEMBLY_TOOL = "cadquery.create_assembly"
FIT_TOOL = "cadquery.check_interference"
CONTRACT_KEY = "IFC-CAD-EDA"
_STAGE_SUBDIR = "_staged_work_products"
MAX_PARTS = 60


class AssemblyFitError(ValueError):
    """A request that cannot be honoured; ``status`` is its HTTP status."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _metric(name: str, *labels: str) -> None:
    try:
        from observability.metrics import collector_for

        getattr(collector_for("metaforge-gateway"), name)(*labels)
    except Exception as exc:  # noqa: BLE001 - a counter never fails the call
        logger.debug("assembly_fit_metric_failed", metric=name, error=str(exc))


def _pid(project_id: str | UUID) -> UUID:
    try:
        return project_id if isinstance(project_id, UUID) else UUID(str(project_id))
    except ValueError as exc:
        raise AssemblyFitError(f"project id {project_id!r} is not a UUID") from exc


def _meta(node: Any) -> dict[str, Any]:
    return dict(getattr(node, "metadata", None) or {})


# ---------------------------------------------------------------------------
# The CAD adapter as a geometry engine
# ---------------------------------------------------------------------------


class McpFitEngine:
    """The :class:`~twin_core.dfa.geometry.GeometryEngine` that asks the CAD adapter."""

    def __init__(self, cad_service: Any) -> None:
        self._cad = cad_service

    async def measure(
        self,
        bodies: Sequence[Body],
        *,
        tolerance_mm3: float,
        distance_pairs: Sequence[tuple[str, str]] = (),
        poses: Sequence[Pose] = (),
    ) -> EngineResult:
        missing = [b.name for b in bodies if not b.file]
        if missing:
            raise AssemblyFitError(f"no STEP staged for {', '.join(missing)}", 409)
        out = await self._cad.invoke_tool(
            FIT_TOOL,
            {
                "parts": [
                    {"name": b.name, "file": b.file, "matrix": flat(b.matrix)} for b in bodies
                ],
                "tolerance_mm3": tolerance_mm3,
                "distance_pairs": [[a, b] for a, b in distance_pairs],
                "poses": [
                    {
                        "id": p.id,
                        "matrices": {k: flat(m) for k, m in p.matrices.items()},
                        "pairs": [[a, b] for a, b in p.pairs],
                    }
                    for p in poses
                ],
            },
        )
        data = out.get("data", out) if isinstance(out, dict) else {}

        def rows(raw: Any) -> list[Overlap]:
            return [
                Overlap(str(r["part_a"]), str(r["part_b"]), float(r.get("volume_mm3") or 0.0))
                for r in raw or []
                if isinstance(r, dict) and r.get("part_a") and r.get("part_b")
            ]

        result = EngineResult(overlaps=rows(data.get("interferences")))
        for r in data.get("distances") or []:
            if isinstance(r, dict) and r.get("part_a") and r.get("part_b"):
                a, b = str(r["part_a"]), str(r["part_b"])
                result.distances[(a, b) if a <= b else (b, a)] = float(r.get("distance_mm") or 0.0)
        for p in data.get("poses") or []:
            if isinstance(p, dict) and p.get("id"):
                result.poses[str(p["id"])] = rows(p.get("interferences"))
        return result


# ---------------------------------------------------------------------------
# Small readers
# ---------------------------------------------------------------------------


def _xy_list(raw: Any) -> list[list[float]]:
    out: list[list[float]] = []
    for p in raw or []:
        if isinstance(p, dict) and p.get("x") is not None and p.get("y") is not None:
            out.append([float(p["x"]), float(p["y"]), float(p.get("z") or 0.0)])
        elif isinstance(p, list | tuple) and len(p) in (2, 3):
            out.append([float(p[0]), float(p[1]), float(p[2]) if len(p) == 3 else 0.0])
    return out


def _wh(raw: Any) -> list[float] | None:
    if isinstance(raw, dict):
        w = raw.get("width") if raw.get("width") is not None else raw.get("length")
        h = raw.get("height") if raw.get("height") is not None else raw.get("depth")
        if isinstance(w, int | float) and isinstance(h, int | float):
            return [float(w), float(h)]
    if isinstance(raw, list | tuple) and len(raw) >= 2:
        try:
            return [float(raw[0]), float(raw[1])]
        except (TypeError, ValueError):
            return None
    return None


def _known(contract: Any, name: str) -> Any:
    p = contract.parameter(name) if contract is not None else None
    return p if p is not None and p.known else None


# ---------------------------------------------------------------------------
# The service
# ---------------------------------------------------------------------------


class AssemblyFitService:
    """What ``twin.build_assembly``, ``twin.check_assembly`` and
    ``twin.accept_release_finding`` call."""

    def __init__(self, twin: Any, *, cad_service: Any = None, workspace_dir: Path | None = None):
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

    def _stage(self, svc: Any, node: Any, key: str) -> str:
        step = svc.read_step(node)
        if not step:
            raise AssemblyFitError(f"{key} has no stored STEP", 409)
        folder = self._root() / _STAGE_SUBDIR / str(node.id)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{key}.step"
        if not path.exists() or path.stat().st_size != len(step):
            path.write_bytes(step)
        return str(path)

    async def _part_revision(self, pid: UUID, ref: str) -> tuple[Any, int, Any]:
        """``(item, revision, node)`` for a ``KEY``/``KEY@n`` or a node id."""
        from twin_core.items import ItemError, item_for_node, resolve_item_ref

        try:
            as_id = UUID(str(ref))
        except ValueError:
            as_id = None
        if as_id is not None:
            node = await self._twin.get_work_product(as_id)
            found = await item_for_node(self._twin, as_id) if node is not None else None
            if node is None or found is None:
                raise AssemblyFitError(f"{ref} is not a revision of a CAD item", 404)
            return found[0], found[1], node
        try:
            item, rev = await resolve_item_ref(self._twin, ref, pid)
        except ItemError as exc:
            raise AssemblyFitError(str(exc), 404) from exc
        node = await self._twin.get_work_product(rev.node_id)
        if node is None:
            raise AssemblyFitError(f"{item.key}@{rev.revision} has no stored revision", 404)
        return item, rev.revision, node

    async def _board_ref(self, node: Any) -> str | None:
        """The ``PCB-...@n`` a board body was exported from, when it says."""
        from twin_core.items import item_for_node

        meta = _meta(node)
        if isinstance(meta.get("board_ref"), str) and meta["board_ref"]:
            return str(meta["board_ref"])
        for raw in meta.get("source_work_product_ids") or []:
            try:
                found = await item_for_node(self._twin, UUID(str(raw)))
            except (ValueError, TypeError):
                continue
            if found is not None and found[0].item_type == "pcb_layout":
                return f"{found[0].key}@{found[1]}"
        return None

    # -- building ---------------------------------------------------------

    async def prepare_assembly(
        self,
        project_id: str | UUID,
        *,
        name: str,
        parts: list[dict[str, Any]],
        joints: list[dict[str, Any]] | None = None,
        material: str | None = None,
    ) -> dict[str, Any]:
        """Resolve, place and build an assembly; the caller commits what comes back.

        Returns the STEP bytes (base64), the ``parts`` for
        ``twin.commit_geometry`` and the layout to keep on the revision.
        """
        from twin_core.dfa.checks import AssemblySpecError, parse_joint

        pid = _pid(project_id)
        if not isinstance(parts, list) or not parts:
            raise AssemblyFitError("parts is a non-empty list of {item_key, location}")
        if len(parts) > MAX_PARTS:
            raise AssemblyFitError(f"at most {MAX_PARTS} parts in one assembly")
        svc = self._cad_service()
        if svc is None:
            _metric("record_assembly_build", "unavailable")
            raise AssemblyFitError("the CAD adapter is not available", 503)
        with tracer.start_as_current_span("twin.build_assembly") as span:
            span.set_attribute("assembly.parts", len(parts))
            layout_parts: list[dict[str, Any]] = []
            adapter_parts: list[dict[str, Any]] = []
            commit_parts: list[dict[str, Any]] = []
            names: set[str] = set()
            for i, spec in enumerate(parts):
                if not isinstance(spec, dict):
                    raise AssemblyFitError(f"parts[{i}] must be an object")
                ref = spec.get("item_key") or spec.get("node_id")
                if not isinstance(ref, str) or not ref.strip():
                    raise AssemblyFitError(f"parts[{i}]: name the part with item_key (CAD-...)")
                item, revision, node = await self._part_revision(pid, ref.strip())
                wp_type = str(getattr(getattr(node, "type", None), "value", node.type))
                if wp_type != "cad_model" or item.item_type not in ("cad_model", "assembly"):
                    raise AssemblyFitError(
                        f"parts[{i}]: {item.key} is a {item.item_type}; place a cad_model (a "
                        "board goes in as the STEP kicad.export_3d made of it)"
                    )
                label = str(spec.get("name") or item.name)
                if label in names:
                    raise AssemblyFitError(f"parts[{i}]: the name {label!r} is used twice")
                names.add(label)
                try:
                    location = spec.get("location") or {}
                    if not isinstance(location, dict):
                        raise ValueError("location must be an object")
                    matrix = location_matrix(location)
                except ValueError as exc:
                    raise AssemblyFitError(f"parts[{i}]: {exc}") from exc
                board_ref = spec.get("board_ref") or await self._board_ref(node)
                file = self._stage(svc, node, item.key)
                item_ref = f"{item.key}@{revision}"
                adapter_parts.append({"name": label, "file": file, "location": location})
                commit_parts.append({"node_id": str(node.id), "name": label})
                entry: dict[str, Any] = {
                    "name": label,
                    "item_ref": item_ref,
                    "node_id": str(node.id),
                    "location": {k: float(v) for k, v in location.items()},
                    "matrix": flat(matrix),
                }
                if board_ref:
                    entry["board_ref"] = str(board_ref)
                layout_parts.append(entry)

            def resolve(text: str) -> str:
                if text in names:
                    return text
                for p in layout_parts:
                    if text.upper() in (p["item_ref"], p["item_ref"].split("@")[0]):
                        return str(p["name"])
                raise AssemblySpecError(f"{text!r} is not a part of this assembly")

            joint_rows: list[dict[str, Any]] = []
            for i, raw in enumerate(joints or []):
                if not isinstance(raw, dict):
                    raise AssemblyFitError(f"joints[{i}] must be an object")
                try:
                    j = parse_joint(raw, resolve, f"joints[{i}]")
                except AssemblySpecError as exc:
                    raise AssemblyFitError(str(exc)) from exc
                joint_rows.append(
                    {
                        "name": j.name,
                        "type": j.joint_type,
                        "parent": j.parent,
                        "moving": j.moving,
                        "axis_point": list(j.axis_point),
                        "axis_dir": list(j.axis_dir),
                        "min_deg": j.min_deg,
                        "max_deg": j.max_deg,
                        "steps": j.steps,
                    }
                )

            # No output_path: the adapter writes into a folder it creates
            # inside its own work folder and returns the path. A folder made
            # here belongs to this process's user (root in the gateway and
            # sidecar images), which the adapter (uid 1000) cannot write into;
            # the adapter used to log "Assembly created" over an empty folder.
            args: dict[str, Any] = {"parts": adapter_parts}
            if material:
                args["material"] = material
            try:
                built = await svc.invoke_tool(ASSEMBLY_TOOL, args)
            except Exception as exc:  # noqa: BLE001 - the adapter's message is the answer
                span.record_exception(exc)
                _metric("record_assembly_build", "failed")
                raise AssemblyFitError(f"{ASSEMBLY_TOOL} failed: {exc}", 502) from exc
            data = built.get("data", built) if isinstance(built, dict) else {}
            written = data.get("assembly_file") if isinstance(data, dict) else None
            if not isinstance(written, str) or not written:
                _metric("record_assembly_build", "failed")
                raise AssemblyFitError(f"{ASSEMBLY_TOOL} returned no assembly_file", 502)
            try:
                from api_gateway.twin.design_files_recorder import resolve_in_workspace

                step = resolve_in_workspace(written, self._root()).read_bytes()
            except (OSError, ValueError) as exc:
                _metric("record_assembly_build", "failed")
                raise AssemblyFitError(f"the assembly STEP could not be read: {exc}", 502) from exc
            if not step:
                _metric("record_assembly_build", "failed")
                raise AssemblyFitError(f"the assembly STEP {written!r} is empty", 502)
            _metric("record_assembly_build", "built")
            logger.info(
                "assembly_built",
                project_id=str(pid),
                name=name,
                parts=len(layout_parts),
                joints=len(joint_rows),
                interferences=len(data.get("interferences") or []),
            )
            return {
                "step_base64": base64.b64encode(step).decode("ascii"),
                "parts": commit_parts,
                "layout": {
                    "parts": layout_parts,
                    "joints": joint_rows,
                    "built_with": ASSEMBLY_TOOL,
                    "build_interferences": data.get("interferences") or [],
                },
                "interference_check_passed": data.get("interference_check_passed"),
                "interferences": data.get("interferences") or [],
            }

    # -- checking ---------------------------------------------------------

    async def _assembly(self, pid: UUID, assembly: str) -> tuple[Any, int, Any]:
        item, revision, node = await self._part_revision(pid, assembly)
        if item.item_type != "assembly":
            raise AssemblyFitError(
                f"{item.key} is a {item.item_type}, not an assembly; build one with "
                "twin.build_assembly",
                409,
            )
        return item, revision, node

    async def _derive(
        self,
        pid: UUID,
        layout_parts: list[dict[str, Any]],
        given: dict[str, Any],
    ) -> tuple[dict[str, Any], set[str], list[str]]:
        """Checks the contract and the parts' metadata imply, the checks that are
        required, and notes on what was derived."""
        from api_gateway.twin.interface_contracts import get_contract
        from twin_core.items import parse_item_ref, resolve_item_ref

        boards = [p for p in layout_parts if p.get("board_ref")]
        if not boards:
            return {}, set(), []
        required = {"hole_pattern", "outline_fit"}
        notes: list[str] = []
        derived: dict[str, Any] = {}
        try:
            stored = await get_contract(self._twin, pid, CONTRACT_KEY)
        except Exception as exc:  # noqa: BLE001 - no contract derives nothing
            logger.debug("assembly_fit_contract_unavailable", error=str(exc))
            stored = None
        contract = stored.contract if stored is not None else None
        link = contract.link("enclosure") if contract is not None else None
        host_key = (link.item_key or "").upper() if link is not None else ""
        host = next(
            (p for p in layout_parts if host_key and p["item_ref"].split("@")[0] == host_key),
            None,
        )
        board = boards[0]
        try:
            key, rev = parse_item_ref(str(board["board_ref"]))
            _item, entry = await resolve_item_ref(self._twin, f"{key}@{rev}" if rev else key, pid)
            pcb_meta = _meta(await self._twin.get_work_product(entry.node_id))
        except Exception as exc:  # noqa: BLE001 - an unreadable board derives nothing
            logger.debug("assembly_fit_board_unavailable", error=str(exc))
            pcb_meta = {}
        host_meta = _meta(await self._twin.get_work_product(UUID(host["node_id"]))) if host else {}
        source = stored.ref if stored is not None else "the parts' metadata"
        if "hole_patterns" not in given and host is not None:
            holes_a = _xy_list(pcb_meta.get("mounting_holes"))
            positions = _known(contract, "mounting_hole_positions")
            if not holes_a and positions is not None:
                holes_a = _xy_list(positions.value)
            holes_b = _xy_list(
                host_meta.get("mounting_bosses")
                or host_meta.get("mounting_standoffs")
                or host_meta.get("mounting_holes")
            )
            if holes_a and holes_b:
                tol = positions.tolerance if positions is not None else None
                derived["hole_patterns"] = [
                    {
                        "a": board["name"],
                        "holes_a": holes_a,
                        "b": host["name"],
                        "holes_b": holes_b,
                        **({"tolerance_mm": tol} if tol is not None else {}),
                        "source": source,
                    }
                ]
                notes.append(f"hole pattern from {source}")
        if "outlines" not in given and host is not None:
            outline = _wh(pcb_meta.get("dimensions")) or _wh(pcb_meta.get("board_outline"))
            agreed = _known(contract, "pcb_outline")
            if outline is None and agreed is not None:
                outline = _wh(agreed.value)
            opening = _wh(host_meta.get("internal_envelope")) or _wh(host_meta.get("pocket"))
            clearance = _known(contract, "pcb_clearance")
            if outline and opening:
                derived["outlines"] = [
                    {
                        "part": board["name"],
                        "size_mm": outline,
                        "host": host["name"],
                        "opening_mm": opening,
                        "clearance_mm": float(clearance.value) if clearance is not None else 0.0,
                        "source": source,
                    }
                ]
                notes.append(f"board outline from {source}")
        if host is None:
            notes.append(
                f"{CONTRACT_KEY} names no enclosure part in this assembly, so the hole pattern "
                "and outline must be given"
            )
        return derived, required, notes

    async def check_assembly(
        self,
        project_id: str | UUID,
        *,
        assembly: str,
        checks: dict[str, Any] | None = None,
        actor: str = "",
    ) -> dict[str, Any]:
        """Check one assembly revision, record the result and return it."""
        from api_gateway.twin.record_pins import link_record, pin_metadata, resolve_record_pins
        from twin_core.dfa.checks import AssemblySpecError, parse_spec, run_checks
        from twin_core.dfm.release_fit import ASSEMBLY_CHECK_TYPE, REPORT_KEY, layout_of
        from twin_core.items import find_item, parse_item_ref
        from twin_core.models.engineering_entity import EngineeringEntity

        pid = _pid(project_id)
        if checks is not None and not isinstance(checks, dict):
            raise AssemblyFitError("checks must be an object")
        svc = self._cad_service()
        with tracer.start_as_current_span("twin.check_assembly") as span:
            item, revision, node = await self._assembly(pid, assembly)
            asm_ref = f"{item.key}@{revision}"
            span.set_attribute("assembly.ref", asm_ref)
            layout = layout_of(_meta(node))
            parts = [p for p in layout.get("parts") or [] if isinstance(p, dict)]
            if not parts:
                _metric("record_assembly_check", "refused")
                raise AssemblyFitError(
                    f"{asm_ref} has no placements on record: build it with twin.build_assembly",
                    409,
                )
            if svc is None:
                _metric("record_assembly_check", "unavailable")
                raise AssemblyFitError("the CAD adapter is not available", 503)
            names = {str(p["name"]) for p in parts}

            def resolve(text: str) -> str:
                if text in names:
                    return text
                for p in parts:
                    if text.upper() in (p["item_ref"], p["item_ref"].split("@")[0]):
                        return str(p["name"])
                raise AssemblySpecError(f"{text!r} is not a part of {asm_ref}")

            bodies: list[Body] = []
            stale: dict[str, str] = {}
            refs: list[str] = [asm_ref]
            for p in parts:
                part_node = await self._twin.get_work_product(UUID(str(p["node_id"])))
                if part_node is None:
                    raise AssemblyFitError(f"{p['item_ref']} is no longer stored", 409)
                matrix: Matrix = (
                    matrix_from(p["matrix"])
                    if p.get("matrix") is not None
                    else location_matrix(p.get("location"))
                )
                key = str(p["item_ref"]).split("@")[0]
                bodies.append(
                    Body(
                        name=str(p["name"]),
                        ref=str(p["item_ref"]),
                        matrix=matrix,
                        file=self._stage(svc, part_node, key),
                    )
                )
                refs.append(str(p["item_ref"]))
                head = await find_item(self._twin, key, pid)
                if head is not None and f"{head.key}@{head.head_revision}" != p["item_ref"]:
                    stale[str(p["name"])] = f"{head.key}@{head.head_revision}"
                if p.get("board_ref"):
                    bkey, _brev = parse_item_ref(str(p["board_ref"]))
                    board = await find_item(self._twin, bkey, pid)
                    refs.append(str(p["board_ref"]))
                    if board is not None and f"{board.key}@{board.head_revision}" != p["board_ref"]:
                        stale.setdefault(
                            str(p["name"]),
                            f"{p['item_ref']} exported from {p['board_ref']}, but the board is "
                            f"now {board.key}@{board.head_revision}",
                        )
            given = dict(checks or {})
            if "joints" not in given and layout.get("joints"):
                given["joints"] = layout["joints"]
            derived, required, notes = await self._derive(pid, parts, given)
            try:
                spec = parse_spec({**derived, **given}, resolve)
            except AssemblySpecError as exc:
                _metric("record_assembly_check", "refused")
                raise AssemblyFitError(str(exc)) from exc
            try:
                report = await run_checks(
                    bodies,
                    spec,
                    McpFitEngine(svc),
                    assembly_ref=asm_ref,
                    stale=stale,
                    required=required,
                )
            except AssemblyFitError:
                raise
            except Exception as exc:  # noqa: BLE001 - the adapter's message is the answer
                span.record_exception(exc)
                _metric("record_assembly_check", "failed")
                raise AssemblyFitError(f"{FIT_TOOL} failed: {exc}", 502) from exc

            pins = await resolve_record_pins(
                self._twin, depends_on=list(dict.fromkeys(refs)), project_id=str(pid)
            )
            body = report.as_dict()
            metadata: dict[str, Any] = {
                "assembly_ref": asm_ref,
                "assembly_key": item.key,
                REPORT_KEY: body,
                "outcome": body["outcome"],
                "counts": body["counts"],
                "checked_by": actor or "agent",
                "derived": notes,
                "checks_given": given,
                **pin_metadata(pins),
            }
            entity = EngineeringEntity(
                entity_type=ASSEMBLY_CHECK_TYPE,
                title=f"Assembly check of {asm_ref}",
                statement=f"Assembly check of {asm_ref}: {body['outcome']} "
                f"({body['counts']['high']} high, {body['counts']['medium']} medium, "
                f"{body['counts']['low']} low)",
                project_id=pid,
                created_by=actor or "twin.check_assembly",
                metadata=metadata,
            )
            created = await self._twin.create_engineering_entity(entity)
            pinned = await link_record(
                self._twin,
                created.id,
                pins,
                record_type=ASSEMBLY_CHECK_TYPE,
                name=entity.title or asm_ref,
                metadata=metadata,
            )
            _metric("record_assembly_check", str(body["outcome"]))
            for f in report.findings():
                _metric("record_assembly_finding", f.check, f.severity)
            span.set_attribute("assembly.outcome", str(body["outcome"]))
            item_view = await self._checklist_item(pid, "assembly_dfa")
            logger.info(
                "assembly_checked",
                project_id=str(pid),
                assembly_ref=asm_ref,
                node_id=str(created.id),
                outcome=body["outcome"],
                **body["counts"],
            )
            return {
                "node_id": str(created.id),
                "assembly_ref": asm_ref,
                "outcome": body["outcome"],
                "counts": body["counts"],
                "open_findings": [f.as_dict() for f in report.open_findings()],
                "checks": body["checks"],
                "derived": notes,
                "checklist_item": item_view,
                **pinned.as_result(),
            }

    async def _checklist_item(self, pid: UUID, item_id: str) -> dict[str, Any] | None:
        from twin_core.dfm.release_checklist import manufacturing_checklist

        try:
            checklist = await manufacturing_checklist(self._twin, pid)
        except Exception as exc:  # noqa: BLE001 - the record is saved; the view is extra
            logger.debug("assembly_fit_checklist_unavailable", error=str(exc))
            return None
        return checklist.item(item_id).as_dict() if checklist is not None else None

    # -- accepting --------------------------------------------------------

    async def accept_release_finding(
        self,
        project_id: str | UUID,
        *,
        item: str,
        targets: list[str],
        reason: str,
        approved_by: str,
        agent: str | None = None,
    ) -> dict[str, Any]:
        """Record a person's (or a delegated agent's) acceptance of failing checks.

        ``item`` is ``eda_checks`` (targets ``PCB-...@n:drc``/``SCH-...@n:erc``)
        or ``assembly_dfa`` (targets the finding ids of the newest check). Only
        a target the checklist currently fails on can be accepted.
        """
        from api_gateway.twin.record_pins import link_record, pin_metadata, resolve_record_pins
        from twin_core.dfa.checks import AssemblyReport
        from twin_core.dfm.release_fit import (
            ASSEMBLY_ITEM,
            EDA_ITEM,
            REPORT_KEY,
            WAIVABLE_ITEMS,
            latest_assembly_check,
        )
        from twin_core.items import list_items
        from twin_core.models.engineering_entity import EngineeringEntity
        from twin_core.models.enums import AuthorityState

        pid = _pid(project_id)
        if item not in WAIVABLE_ITEMS:
            raise AssemblyFitError(
                f"item is one of {', '.join(sorted(WAIVABLE_ITEMS))}; fabrication outputs are "
                "made again, never accepted"
            )
        clean = [t.strip() for t in targets or [] if isinstance(t, str) and t.strip()]
        if not clean:
            raise AssemblyFitError("targets names what is accepted")
        if not reason or not reason.strip():
            raise AssemblyFitError("reason is required")
        if not approved_by or not approved_by.strip():
            raise AssemblyFitError("no person approved this acceptance", 403)
        with tracer.start_as_current_span("twin.accept_release_finding") as span:
            span.set_attribute("release.item", item)
            pin_refs: list[str] = []
            if item == EDA_ITEM:
                heads = {
                    f"{i.key}@{i.head_revision}:{'erc' if t == 'schematic' else 'drc'}"
                    for t in ("schematic", "pcb_layout")
                    for i in await list_items(self._twin, project_id=pid, item_type=t)
                }
                unknown = [t for t in clean if t.lower() not in {h.lower() for h in heads}]
                if unknown:
                    raise AssemblyFitError(
                        f"not a current ERC or DRC target: {', '.join(unknown)}; targets are "
                        + (", ".join(sorted(heads)) or "none (no schematic or board)"),
                        409,
                    )
                clean = [next(h for h in heads if h.lower() == t.lower()) for t in clean]
                pin_refs = [t.rsplit(":", 1)[0] for t in clean]
            else:
                found: dict[str, Any] = {}
                for asm in await list_items(self._twin, project_id=pid, item_type="assembly"):
                    ref = f"{asm.key}@{asm.head_revision}"
                    record = await latest_assembly_check(self._twin, pid, ref)
                    if record is None:
                        continue
                    report = AssemblyReport.from_dict(_meta(record).get(REPORT_KEY) or {})
                    for f in report.open_findings():
                        found[f.id] = (ref, f)
                unknown = [t for t in clean if t not in found]
                if unknown:
                    raise AssemblyFitError(
                        f"not an open finding of a current assembly check: {', '.join(unknown)}",
                        409,
                    )
                for t in clean:
                    ref, f = found[t]
                    pin_refs.extend([ref, *f.parts])
            pins = await resolve_record_pins(
                self._twin, depends_on=list(dict.fromkeys(pin_refs)), project_id=str(pid)
            )
            delegated = bool(agent)
            metadata: dict[str, Any] = {
                "release_check": item,
                "targets": clean,
                "reason": reason.strip()[:1000],
                "approved_by": f"agent:{agent}" if delegated else approved_by.strip(),
                "approval_route": "delegated" if delegated else "person",
                **({"agent": agent, "on_behalf_of": approved_by.strip()} if delegated else {}),
                **pin_metadata(pins),
            }
            entity = EngineeringEntity(
                entity_type="waiver",
                title=f"Accepted for release: {', '.join(clean)}"[:200],
                statement=reason.strip()[:2000],
                project_id=pid,
                authority=AuthorityState.APPROVED,
                created_by=metadata["approved_by"],
                metadata=metadata,
            )
            created = await self._twin.create_engineering_entity(entity)
            await link_record(
                self._twin,
                created.id,
                pins,
                record_type="waiver",
                name=entity.title or "waiver",
                metadata=metadata,
            )
            _metric("record_release_finding_accepted", item, metadata["approval_route"])
            logger.info(
                "release_finding_accepted",
                project_id=str(pid),
                item=item,
                targets=clean,
                route=metadata["approval_route"],
                agent=agent,
                on_behalf_of=approved_by if delegated else None,
            )
            return {
                "node_id": str(created.id),
                "item": item,
                "targets": clean,
                "approved_by": metadata["approved_by"],
                "on_behalf_of": approved_by.strip() if delegated else None,
                "depends_on": [p.ref for p in pins],
                "checklist_item": await self._checklist_item(
                    pid, ASSEMBLY_ITEM if item == ASSEMBLY_ITEM else EDA_ITEM
                ),
            }


__all__ = [
    "ASSEMBLY_TOOL",
    "FIT_TOOL",
    "AssemblyFitError",
    "AssemblyFitService",
    "McpFitEngine",
]
