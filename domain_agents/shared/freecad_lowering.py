"""FreeCAD Lowering Pass (requirements doc §6.6.2).

Materializes a ``DesignIR`` document against the real FreeCAD session API,
one MCP call per entity, resolving IR-level ``*_ref`` fields to FreeCAD
``obj_id``s as it goes, then measures and exports the document's terminal
entity to STEP.

FreeCAD only. There is no CadQuery Lowering Pass yet -- that side is a real
compiler (topologically sort the IR subgraph, flatten it into a linear
script), genuinely separate, larger work.

v1 scope cuts, each raising ``LoweringError`` rather than guessing or
silently dropping data:

- **No `create_parametric`.** ``freecad.create_parametric`` is the legacy
  file-based tool (no ``session_id``) -- it has no session-based equivalent,
  so it doesn't fit the one-session-call-per-entity model every other op
  uses here.
- **No assembly/multi-body export.** `place`/`joint`/`create_assembly`
  entities lower fine (so a document mixing a body and an assembly doesn't
  hard-fail), but the terminal entity that gets measured/exported must be a
  single exportable solid, not an assembly.
- **No `rotation` on `transform`, no `orientation` on `place`.** The real
  ``freecad.transform_object``/``freecad.add_part_to_assembly`` tools take
  ``position`` only (confirmed against ``adapter.py``); guessing a rotation
  dict shape that doesn't match what FreeCAD actually expects would silently
  produce wrong geometry, worse than rejecting it outright.
- **No checkpoint cache, no incremental re-lowering (§6.6.3).** The whole
  document is lowered from scratch every call.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field

import structlog

from observability.tracing import get_tracer
from skill_registry.mcp_bridge import McpBridge
from twin_core.design_ir.models import (
    BooleanEntity,
    ChamferEdgesEntity,
    ChamferEntity,
    CreateAssemblyEntity,
    CreateBodyEntity,
    CreatePrimitiveEntity,
    DesignIR,
    FilletEdgesEntity,
    FilletEntity,
    IREntity,
    JointEntity,
    LinearPatternEntity,
    LoftEntity,
    MirrorEntity,
    PadEntity,
    PlaceEntity,
    PocketEntity,
    PolarPatternEntity,
    RevolveEntity,
    ShellEntity,
    SketchEntity,
    SweepEntity,
    TransformEntity,
)
from twin_core.design_ir.validation import validate_design_ir

logger = structlog.get_logger(__name__)
tracer = get_tracer("domain_agents.shared.freecad_lowering")

_NO_SHAPE_OPS = frozenset({"create_body", "create_sketch", "create_assembly"})
_UNSUPPORTED_OPS = frozenset({"create_parametric"})


class LoweringError(Exception):
    """The Design IR document could not be lowered against FreeCAD."""


@dataclass
class FreecadLoweringResult:
    """Result of lowering one ``DesignIR`` document against FreeCAD."""

    step_bytes: bytes
    volume_mm3: float
    surface_area_mm2: float
    bounding_box: dict[str, float]
    terminal_entity_id: str
    obj_id_map: dict[str, str] = field(default_factory=dict)


def _find_terminal_entity(doc: DesignIR) -> IREntity:
    """The last entity with its own exportable shape.

    ``create_body``/``create_sketch``/``create_assembly`` don't have a
    standalone shape at the point they're created, so a document ending in
    one of those has nothing to export -- raise rather than guess which
    earlier entity was "really" the result.
    """
    for entity in reversed(doc.entities):
        if entity.op not in _NO_SHAPE_OPS:
            return entity
    raise LoweringError(
        "no exportable terminal entity: the document is empty, or ends in "
        "create_body/create_sketch/create_assembly, none of which have a "
        "standalone shape to measure or export"
    )


async def lower_design_ir_freecad(mcp: McpBridge, doc: DesignIR) -> FreecadLoweringResult:
    """Materialize every entity in ``doc`` via the FreeCAD session API.

    Always opens and closes exactly one session, mirroring
    ``generate_cad_script/handler.py``'s ``_run_freecad_code`` cleanup
    discipline (session close is best-effort in a ``finally``, a stuck
    session must never mask a successful export).

    Raises:
        LoweringError: Invalid IR, an unsupported op, or no exportable
            terminal entity. Raised before any MCP call, so a bad document
            never opens a session at all.
    """
    with tracer.start_as_current_span("freecad_lowering.lower") as span:
        span.set_attribute("entity_count", len(doc.entities))

        errors = validate_design_ir(doc)
        if errors:
            raise LoweringError(f"invalid Design IR document: {'; '.join(errors)}")

        unsupported = [e.id for e in doc.entities if e.op in _UNSUPPORTED_OPS]
        if unsupported:
            raise LoweringError(
                f"unsupported op(s) for the FreeCAD Lowering Pass: {unsupported} "
                "(create_parametric is the legacy file-based tool, no session equivalent)"
            )

        terminal = _find_terminal_entity(doc)
        span.set_attribute("terminal_entity_id", terminal.id)

        session = await mcp.invoke("freecad.open_session", {}, timeout=60)
        session_id = session.get("session_id")
        if not session_id:
            raise LoweringError("freecad.open_session did not return a session_id")

        obj_ids: dict[str, str] = {}
        try:
            for entity in doc.entities:
                obj_ids[entity.id] = await _lower_one(mcp, session_id, entity, obj_ids)

            terminal_obj_id = obj_ids[terminal.id]
            measurements = await mcp.invoke(
                "freecad.measure",
                {"session_id": session_id, "obj_id": terminal_obj_id},
                timeout=60,
            )
            export_result = await mcp.invoke(
                "freecad.export_model",
                {"session_id": session_id, "obj_id": terminal_obj_id},
                timeout=120,
            )
            step_b64 = export_result.get("step_base64")
            if not step_b64:
                raise LoweringError("freecad.export_model returned no step_base64")
        finally:
            try:
                await mcp.invoke("freecad.close_session", {"session_id": session_id}, timeout=30)
            except Exception as exc:  # noqa: BLE001 -- cleanup is best-effort
                logger.warning(
                    "freecad_lowering_session_close_failed", session_id=session_id, error=str(exc)
                )

        content = base64.b64decode(step_b64)
        bounding_box = measurements.get("bounding_box", {})
        span.set_attribute("volume_mm3", float(measurements.get("volume_mm3", 0.0)))

        return FreecadLoweringResult(
            step_bytes=content,
            volume_mm3=float(measurements.get("volume_mm3", 0.0)),
            surface_area_mm2=float(measurements.get("surface_area_mm2", 0.0)),
            bounding_box=bounding_box,
            terminal_entity_id=terminal.id,
            obj_id_map=obj_ids,
        )


async def _lower_one(
    mcp: McpBridge, session_id: str, entity: IREntity, obj_ids: dict[str, str]
) -> str:
    """Dispatch one entity to its FreeCAD session tool call.

    Returns the FreeCAD ``obj_id`` this entity's id should map to -- either a
    freshly registered object, or (for ``transform``/`place`, which mutate in
    place and register nothing new, confirmed against ``adapter.py``) the
    already-resolved ``obj_id`` of the object they acted on.
    """
    if isinstance(entity, CreatePrimitiveEntity):
        result = await mcp.invoke(
            "freecad.create_primitive",
            {
                "session_id": session_id,
                "kind": entity.kind,
                "parameters": entity.parameters,
                "name": entity.name,
            },
        )
        return str(result["obj_id"])

    if isinstance(entity, CreateBodyEntity):
        result = await mcp.invoke(
            "freecad.create_body", {"session_id": session_id, "name": entity.name}
        )
        return str(result["obj_id"])

    if isinstance(entity, SketchEntity):
        result = await mcp.invoke(
            "freecad.create_sketch",
            {
                "session_id": session_id,
                "body_id": obj_ids[entity.body_ref],
                "plane": entity.plane,
                "offset": entity.offset,
                "elements": [el.model_dump() for el in entity.elements],
            },
        )
        return str(result["obj_id"])

    if isinstance(entity, PadEntity):
        result = await mcp.invoke(
            "freecad.pad_sketch",
            {
                "session_id": session_id,
                "body_id": obj_ids[entity.body_ref],
                "sketch_id": obj_ids[entity.sketch_ref],
                "length": entity.depth,
                "reversed": entity.reversed,
                "midplane": entity.midplane,
            },
        )
        return str(result["obj_id"])

    if isinstance(entity, PocketEntity):
        result = await mcp.invoke(
            "freecad.pocket_sketch",
            {
                "session_id": session_id,
                "body_id": obj_ids[entity.body_ref],
                "sketch_id": obj_ids[entity.sketch_ref],
                "depth": entity.depth,
                "reversed": entity.reversed,
            },
        )
        return str(result["obj_id"])

    if isinstance(entity, RevolveEntity):
        result = await mcp.invoke(
            "freecad.revolve_sketch",
            {
                "session_id": session_id,
                "body_id": obj_ids[entity.body_ref],
                "sketch_id": obj_ids[entity.sketch_ref],
                "angle": entity.angle,
                "axis": entity.axis,
                "reversed": entity.reversed,
            },
        )
        return str(result["obj_id"])

    if isinstance(entity, LoftEntity):
        result = await mcp.invoke(
            "freecad.loft_sketches",
            {
                "session_id": session_id,
                "body_id": obj_ids[entity.body_ref],
                "profile_id": obj_ids[entity.profile_ref],
                "section_ids": [obj_ids[r] for r in entity.section_refs],
            },
        )
        return str(result["obj_id"])

    if isinstance(entity, SweepEntity):
        result = await mcp.invoke(
            "freecad.sweep_sketch",
            {
                "session_id": session_id,
                "body_id": obj_ids[entity.body_ref],
                "profile_id": obj_ids[entity.profile_ref],
                "path_id": obj_ids[entity.path_ref],
            },
        )
        return str(result["obj_id"])

    if isinstance(entity, FilletEdgesEntity):
        result = await mcp.invoke(
            "freecad.fillet_edges",
            {
                "session_id": session_id,
                "body_id": obj_ids[entity.body_ref],
                "radius": entity.radius,
                "edges": entity.edge_selectors or None,
            },
        )
        return str(result["obj_id"])

    if isinstance(entity, ChamferEdgesEntity):
        result = await mcp.invoke(
            "freecad.chamfer_edges",
            {
                "session_id": session_id,
                "body_id": obj_ids[entity.body_ref],
                "size": entity.distance,
                "edges": entity.edge_selectors or None,
            },
        )
        return str(result["obj_id"])

    if isinstance(entity, LinearPatternEntity):
        result = await mcp.invoke(
            "freecad.linear_pattern",
            {
                "session_id": session_id,
                "body_id": obj_ids[entity.body_ref],
                "feature_id": obj_ids[entity.source_ref],
                "count": entity.count,
                "spacing": entity.spacing,
                "axis": entity.axis,
            },
        )
        return str(result["obj_id"])

    if isinstance(entity, PolarPatternEntity):
        result = await mcp.invoke(
            "freecad.polar_pattern",
            {
                "session_id": session_id,
                "body_id": obj_ids[entity.body_ref],
                "feature_id": obj_ids[entity.source_ref],
                "count": entity.count,
                "angle": entity.angle,
                "axis": entity.axis,
            },
        )
        return str(result["obj_id"])

    if isinstance(entity, MirrorEntity):
        result = await mcp.invoke(
            "freecad.mirror_feature",
            {
                "session_id": session_id,
                "body_id": obj_ids[entity.body_ref],
                "feature_id": obj_ids[entity.source_ref],
                "plane": entity.plane,
            },
        )
        return str(result["obj_id"])

    if isinstance(entity, ShellEntity):
        result = await mcp.invoke(
            "freecad.shell_solid",
            {
                "session_id": session_id,
                "body_id": obj_ids[entity.body_ref],
                "thickness": entity.thickness,
                "faces": entity.face_selectors or None,
            },
        )
        return str(result["obj_id"])

    if isinstance(entity, BooleanEntity):
        if len(entity.tool_refs) != 1:
            raise LoweringError(
                f"entity {entity.id!r}: FreeCAD's session boolean takes exactly one "
                f"tool_ref per call, got {len(entity.tool_refs)} -- emit chained "
                "booleans as separate entities instead"
            )
        result = await mcp.invoke(
            "freecad.boolean",
            {
                "session_id": session_id,
                "obj_a": obj_ids[entity.base_ref],
                "obj_b": obj_ids[entity.tool_refs[0]],
                "operation": entity.operation,
            },
        )
        return str(result["obj_id"])

    if isinstance(entity, FilletEntity):
        result = await mcp.invoke(
            "freecad.fillet",
            {
                "session_id": session_id,
                "obj_id": obj_ids[entity.target_ref],
                "radius": entity.radius,
            },
        )
        return str(result["obj_id"])

    if isinstance(entity, ChamferEntity):
        result = await mcp.invoke(
            "freecad.chamfer",
            {
                "session_id": session_id,
                "obj_id": obj_ids[entity.target_ref],
                "distance": entity.distance,
            },
        )
        return str(result["obj_id"])

    if isinstance(entity, TransformEntity):
        if entity.rotation is not None:
            raise LoweringError(
                f"entity {entity.id!r}: rotation is not supported by this Lowering "
                "Pass yet (freecad.transform_object's real rotation argument shape "
                "isn't confirmed; position-only for now)"
            )
        target_obj_id = obj_ids[entity.target_ref]
        await mcp.invoke(
            "freecad.transform_object",
            {
                "session_id": session_id,
                "obj_id": target_obj_id,
                "position": list(entity.position),
            },
        )
        return target_obj_id  # mutates in place, no new obj_id (confirmed)

    if isinstance(entity, CreateAssemblyEntity):
        result = await mcp.invoke(
            "freecad.create_assembly", {"session_id": session_id, "name": entity.name}
        )
        return str(result["obj_id"])

    if isinstance(entity, PlaceEntity):
        if entity.orientation is not None:
            raise LoweringError(
                f"entity {entity.id!r}: orientation is not supported by this Lowering "
                "Pass yet (freecad.add_part_to_assembly takes position only)"
            )
        part_obj_id = obj_ids[entity.part_ref]
        await mcp.invoke(
            "freecad.add_part_to_assembly",
            {
                "session_id": session_id,
                "assembly_id": obj_ids[entity.assembly_ref],
                "part_id": part_obj_id,
                "position": list(entity.position),
            },
        )
        return part_obj_id  # no new obj_id (confirmed)

    if isinstance(entity, JointEntity):
        result = await mcp.invoke(
            "freecad.add_assembly_joint",
            {
                "session_id": session_id,
                "assembly_id": obj_ids[entity.assembly_ref],
                "base_id": obj_ids[entity.part_a_ref],
                "follower_id": obj_ids[entity.part_b_ref],
                "type": entity.joint_type,
                "axis": list(entity.axis) if entity.axis else None,
                "anchor": list(entity.anchor) if entity.anchor else None,
            },
        )
        return str(result["obj_id"])

    raise LoweringError(f"entity {entity.id!r}: no FreeCAD lowering for op {entity.op!r}")
