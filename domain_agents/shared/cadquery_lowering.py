"""CadQuery Lowering Pass (requirements doc §6.6.2, MET-689).

Compiles a ``DesignIR`` document into a single, flattened CadQuery Python
script. Unlike ``freecad_lowering.py`` (one MCP call per entity, against a
live session), CadQuery has no session/document concept -- the whole
document is topologically walked (the schema already guarantees
``doc.entities`` is in a valid topological order, same guarantee
``freecad_lowering.py`` relies on) and flattened into ONE generated script,
executed via a single ``cadquery.execute_script`` call.

v1 scope cuts, each raising ``LoweringError`` rather than guessing or
silently dropping data (same discipline as the FreeCAD sibling):

- **Narrower op coverage than FreeCAD's lowering pass.** Only
  ``create_primitive`` (box/cylinder/sphere), ``transform`` (position
  only), ``boolean``, ``sketch`` (rectangle/circle/line -- not arc), and
  ``pad``/``pocket`` are implemented. ``revolve``/``loft``/``sweep``/
  ``fillet_edges``/``chamfer_edges``/``linear_pattern``/``polar_pattern``/
  ``mirror``/``shell``/``fillet``/``chamfer``/``create_parametric`` all
  raise cleanly -- this is a first v1 slice covering what's actually been
  exercised live, not a claim that CadQuery can't do more.
- **No cone/torus primitives yet** -- box/cylinder/sphere only.
- **No rotation on transform** -- same restriction, same reasoning as
  FreeCAD's sibling (the real ``.rotate()`` argument shape needs
  confirming against a live adapter before guessing at it).
- **Boolean takes exactly one tool_ref per call** -- same restriction as
  FreeCAD's lowering, so one IR document lowers identically regardless of
  which adapter executes it.
- **No multi-body assemblies.** ``create_assembly``/``place``/``joint``
  entities validate and pass through (a document mixing a body and an
  assembly doesn't hard-fail) but emit no script lines and are excluded
  from terminal-entity selection -- same v1 cut as FreeCAD's sibling.
- **A sketch's ``body_ref`` only supplies FreeCAD-side placement
  context.** CadQuery workplanes are built directly from the sketch's own
  ``plane``/``offset``, ignoring ``body_ref``'s position -- correct for
  the single-body documents this v1 targets, wrong for a second body
  meant to sit relative to a first (the same "no multi-body assembly
  export" limit FreeCAD's v1 already accepts).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import structlog

from observability.tracing import get_tracer
from skill_registry.mcp_bridge import McpBridge
from twin_core.design_ir.models import (
    BooleanEntity,
    CreateAssemblyEntity,
    CreateBodyEntity,
    CreatePrimitiveEntity,
    DesignIR,
    IREntity,
    JointEntity,
    PadEntity,
    PlaceEntity,
    PocketEntity,
    SketchArc,
    SketchCircle,
    SketchEntity,
    SketchLine,
    SketchRectangle,
    TransformEntity,
)
from twin_core.design_ir.validation import validate_design_ir

logger = structlog.get_logger(__name__)
tracer = get_tracer("domain_agents.shared.cadquery_lowering")

# MET-689: the real op literals are "sketch"/"create_assembly"/"joint" (see
# twin_core/design_ir/models.py) -- NOT "create_sketch". freecad_lowering.py's
# own _NO_SHAPE_OPS has had "create_sketch" (which never matches any real
# entity's .op) instead of "sketch" since MET-642, a latent bug that lets a
# document dangling on an unused sketch entity slip through terminal-entity
# selection uncaught -- confirmed while building this sibling, tracked
# separately (not fixed here, out of scope for this new module).
_NO_SHAPE_OPS = frozenset({"create_body", "sketch", "create_assembly", "joint"})
_UNSUPPORTED_OPS = frozenset(
    {
        "create_parametric",
        "revolve",
        "loft",
        "sweep",
        "fillet_edges",
        "chamfer_edges",
        "linear_pattern",
        "polar_pattern",
        "mirror",
        "shell",
        "fillet",
        "chamfer",
    }
)
_SUPPORTED_PRIMITIVE_KINDS = frozenset({"box", "cylinder", "sphere"})
_BOOLEAN_OPS = {"union": "union", "subtract": "cut", "intersect": "intersect"}


class LoweringError(Exception):
    """The Design IR document could not be lowered against CadQuery."""


@dataclass
class CadqueryLoweringResult:
    """Result of lowering one ``DesignIR`` document against CadQuery."""

    step_bytes: bytes
    volume_mm3: float
    surface_area_mm2: float
    bounding_box: dict[str, float]
    terminal_entity_id: str
    obj_id_map: dict[str, str] = field(default_factory=dict)
    script_text: str = ""


def _var_name(entity_id: str) -> str:
    """A valid, collision-safe Python identifier for one IR entity's script variable."""
    safe = "".join(c if c.isalnum() or c == "_" else "_" for c in entity_id)
    if not safe or safe[0].isdigit():
        safe = f"e_{safe}"
    return f"v_{safe}"


def _fmt(value: float) -> str:
    return repr(float(value))


def _find_terminal_entity(doc: DesignIR) -> IREntity:
    """The last entity with its own exportable shape.

    ``create_body``/``sketch``/``create_assembly``/``joint`` don't have a
    standalone shape -- a sketch is an unextruded profile, the others are
    pure containers/metadata -- so a document ending in one of those has
    nothing to export.
    """
    for entity in reversed(doc.entities):
        if entity.op not in _NO_SHAPE_OPS:
            return entity
    raise LoweringError(
        "no exportable terminal entity: the document is empty, or ends in "
        "create_body/sketch/create_assembly/joint, none of which have a "
        "standalone shape to export"
    )


def _lower_sketch_profile(var: str, entity: SketchEntity) -> list[str]:
    """Script lines building a CadQuery Workplane profile (not yet extruded)."""
    lines = [f"{var} = cq.Workplane({entity.plane!r}).workplane(offset={_fmt(entity.offset)})"]
    has_line = False
    for el in entity.elements:
        if isinstance(el, SketchRectangle):
            cx = el.origin[0] + el.width / 2.0
            cy = el.origin[1] + el.height / 2.0
            lines.append(
                f"{var} = {var}.moveTo({_fmt(cx)}, {_fmt(cy)})"
                f".rect({_fmt(el.width)}, {_fmt(el.height)})"
            )
        elif isinstance(el, SketchCircle):
            lines.append(
                f"{var} = {var}.moveTo({_fmt(el.center[0])}, {_fmt(el.center[1])})"
                f".circle({_fmt(el.radius)})"
            )
        elif isinstance(el, SketchLine):
            has_line = True
            lines.append(
                f"{var} = {var}.moveTo({_fmt(el.start[0])}, {_fmt(el.start[1])})"
                f".lineTo({_fmt(el.end[0])}, {_fmt(el.end[1])})"
            )
        elif isinstance(el, SketchArc):
            raise LoweringError(
                f"entity {entity.id!r}: SketchArc is not supported by this Lowering Pass "
                "yet (same v1 cut as the FreeCAD Lowering Pass -- no arc-to-CadQuery "
                "translation implemented)"
            )
        else:  # pragma: no cover -- exhaustive per SketchElement's discriminated union
            raise LoweringError(f"entity {entity.id!r}: unsupported sketch element {el!r}")
    if has_line:
        lines.append(f"{var} = {var}.close()")
    return lines


async def lower_design_ir_cadquery(mcp: McpBridge, doc: DesignIR) -> CadqueryLoweringResult:
    """Compile ``doc`` into one CadQuery script and execute it.

    Raises:
        LoweringError: Invalid IR, an unsupported op, or no exportable
            terminal entity. Raised before any MCP call, so a bad
            document never invokes the adapter at all.
    """
    with tracer.start_as_current_span("cadquery_lowering.lower") as span:
        span.set_attribute("entity_count", len(doc.entities))

        errors = validate_design_ir(doc)
        if errors:
            raise LoweringError(f"invalid Design IR document: {'; '.join(errors)}")

        unsupported = [e.id for e in doc.entities if e.op in _UNSUPPORTED_OPS]
        if unsupported:
            raise LoweringError(
                f"unsupported op(s) for the CadQuery Lowering Pass: {unsupported} "
                f"(v1 covers create_primitive/transform/boolean/sketch/pad/pocket only)"
            )

        terminal = _find_terminal_entity(doc)
        span.set_attribute("terminal_entity_id", terminal.id)

        script_lines = ["import cadquery as cq"]
        var_map: dict[str, str] = {}
        body_current: dict[str, str] = {}

        for entity in doc.entities:
            var = _lower_one(entity, var_map, body_current, script_lines)
            if var is not None:
                var_map[entity.id] = var

        terminal_var = var_map.get(terminal.id)
        if terminal_var is None:
            raise LoweringError(f"terminal entity {terminal.id!r} produced no CadQuery variable")
        script_lines.append(f"result = {terminal_var}")
        script = "\n".join(script_lines)

        response = await mcp.invoke("cadquery.execute_script", {"script": script}, timeout=120)
        step_b64 = response.get("step_base64")
        if not step_b64:
            raise LoweringError("cadquery.execute_script returned no step_base64")

        import base64

        content = base64.b64decode(step_b64)
        bounding_box = response.get("bounding_box", {})
        span.set_attribute("volume_mm3", float(response.get("volume_mm3", 0.0)))

        return CadqueryLoweringResult(
            step_bytes=content,
            volume_mm3=float(response.get("volume_mm3", 0.0)),
            surface_area_mm2=float(response.get("surface_area_mm2", 0.0)),
            bounding_box=bounding_box,
            terminal_entity_id=terminal.id,
            obj_id_map=dict(var_map),
            script_text=script,
        )


def _lower_one(
    entity: IREntity,
    var_map: dict[str, str],
    body_current: dict[str, str],
    script_lines: list[str],
) -> str | None:
    """Append this entity's script line(s); return its script variable name
    (or ``None`` for entities that don't produce one -- ``create_body``,
    ``create_assembly``, ``place``, ``joint``)."""
    var = _var_name(entity.id)

    if isinstance(entity, CreatePrimitiveEntity):
        if entity.kind not in _SUPPORTED_PRIMITIVE_KINDS:
            raise LoweringError(
                f"entity {entity.id!r}: primitive kind {entity.kind!r} is not supported by "
                f"this Lowering Pass yet (v1 covers {sorted(_SUPPORTED_PRIMITIVE_KINDS)})"
            )
        p = entity.parameters
        if entity.kind == "box":
            script_lines.append(
                f"{var} = cq.Workplane().box({_fmt(p['length'])}, {_fmt(p['width'])}, "
                f"{_fmt(p['height'])}, centered=(False, False, False))"
            )
        elif entity.kind == "cylinder":
            script_lines.append(
                f"{var} = cq.Workplane().cylinder({_fmt(p['height'])}, {_fmt(p['radius'])}, "
                "centered=(True, True, False))"
            )
        else:  # sphere
            script_lines.append(f"{var} = cq.Workplane().sphere({_fmt(p['radius'])})")
        return var

    if isinstance(entity, CreateBodyEntity):
        return None

    if isinstance(entity, SketchEntity):
        script_lines.extend(_lower_sketch_profile(var, entity))
        return var

    if isinstance(entity, PadEntity):
        sketch_var = var_map[entity.sketch_ref]
        depth = -entity.depth if entity.reversed else entity.depth
        script_lines.append(f"{var} = {sketch_var}.extrude({_fmt(depth)}, both={entity.midplane})")
        body_current[entity.body_ref] = var
        return var

    if isinstance(entity, PocketEntity):
        current = body_current.get(entity.body_ref)
        if current is None:
            raise LoweringError(
                f"entity {entity.id!r}: pocket on body {entity.body_ref!r} with no existing "
                "solid yet -- pad a sketch first"
            )
        sketch_var = var_map[entity.sketch_ref]
        cutter_var = f"{var}_cutter"
        # A pocket cuts INTO existing material below the sketch plane -- the
        # opposite direction from pad's default growth (away from the sketch
        # plane, into empty space). Confirmed live (MET-691 e2e run): using
        # pad's same sign here extrudes the cutter away from the body with
        # zero overlap, so .cut() removes nothing -- volume came back
        # unchanged (24000.0, the un-pocketed box exactly).
        depth = entity.depth if entity.reversed else -entity.depth
        script_lines.append(f"{cutter_var} = {sketch_var}.extrude({_fmt(depth)})")
        script_lines.append(f"{var} = {current}.cut({cutter_var})")
        body_current[entity.body_ref] = var
        return var

    if isinstance(entity, BooleanEntity):
        if len(entity.tool_refs) != 1:
            raise LoweringError(
                f"entity {entity.id!r}: CadQuery lowering takes exactly one tool_ref per "
                f"boolean entity (same restriction as the FreeCAD Lowering Pass), got "
                f"{len(entity.tool_refs)} -- emit chained booleans as separate entities instead"
            )
        base_var = var_map[entity.base_ref]
        tool_var = var_map[entity.tool_refs[0]]
        method = _BOOLEAN_OPS[entity.operation]
        script_lines.append(f"{var} = {base_var}.{method}({tool_var})")
        return var

    if isinstance(entity, TransformEntity):
        if entity.rotation is not None:
            raise LoweringError(
                f"entity {entity.id!r}: rotation is not supported by this Lowering Pass "
                "yet (position-only for now, same restriction as the FreeCAD sibling)"
            )
        target_var = var_map[entity.target_ref]
        script_lines.append(f"{var} = {target_var}.translate({tuple(entity.position)!r})")
        # CadQuery objects are immutable (.translate() returns a NEW Workplane,
        # unlike FreeCAD's in-place Placement mutation) -- update BOTH this
        # entity's own id and target_ref's id to point at the post-transform
        # variable, so a later *_ref to either one resolves to the current
        # position (matches FreeCAD's observable "mutates in place" behavior
        # even though the underlying mechanism differs).
        var_map[entity.target_ref] = var
        return var

    if isinstance(entity, CreateAssemblyEntity | PlaceEntity | JointEntity):
        return None

    raise LoweringError(f"entity {entity.id!r}: no CadQuery lowering for op {entity.op!r}")
