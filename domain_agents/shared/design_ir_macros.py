"""Parametric feature library -- Design IR macros (FORGE-269, gap G-D1).

A "macro" is nothing more than a Python function that emits a real Design
IR entity sequence (the same ``list[dict]`` shape
``generate_cad_ir``/``twin_core.design_ir.DesignIR`` already validates and
lowers) for one named, reusable, parametrized feature. No new compiler or
lowering work: a macro composes EXISTING entity ops
(``create_body``/``sketch``/``pad``/``pocket``/``polar_pattern``, per
``twin_core/design_ir/models.py``) the same way an agent hand-authoring a
Design IR document already could -- this module just names and validates
the common shapes so nobody has to re-derive them from scratch each time.

Pure, no twin/MCP dependency (mirrors ``cadquery_lowering.py``'s own
split) -- ``domain_agents.mechanical.skills.generate_parametric_feature``
is the thin orchestration layer that calls a macro, then delegates
lowering + commit to the SAME ``generate_cad_ir`` skill every other
Design IR document already goes through (zero duplication of that logic).

v1 covers two of the ticket's seven named features -- ``bolt_pattern`` and
``rib`` -- chosen because both compose cleanly from entities that already
exist and need no external reference data (unlike ``bearing_seat``/
``motor_mount``, which need real vendor/datasheet dimensions this module
has no source for; ``clevis_yoke``, nontrivial fork geometry; ``gear_stage``,
real involute gear math -- a substantial separate capability, not an
entity composition; ``cable_channel``, undefined routing-curve semantics).
Each deferred feature is the same pattern: a new function here plus one
new ``Literal`` value on the skill's ``feature_type`` -- growing the
library never needs a new skill or a schema migration.

``bolt_pattern`` needs ``twin_core.design_ir.models.PolarPatternEntity``,
which the CadQuery Lowering Pass does not support (see that module's own
docstring: "Still no linear_pattern/polar_pattern/create_parametric") --
callers must use the FreeCAD adapter (``generate_cad_ir``'s own default)
for this feature. ``rib`` uses only ``create_body``/``sketch``/``pad``,
supported by both adapters.
"""

from __future__ import annotations

from typing import Any


def bolt_pattern_entities(
    *,
    plate_length_mm: float,
    plate_width_mm: float,
    plate_thickness_mm: float,
    hole_diameter_mm: float,
    hole_count: int,
    pattern_radius_mm: float,
) -> list[dict[str, Any]]:
    """A rectangular mounting plate with ``hole_count`` through-holes evenly
    spaced on a circle of radius ``pattern_radius_mm`` about the plate's
    center -- the common "bolt circle" mounting pattern (motor faces,
    bearing flanges, panel mounts).

    Requires ``pattern_radius_mm`` to leave real material around each hole
    (checked structurally, not just geometrically-plausible-sounding):
    the hole circle must fit inside the plate with margin for the hole
    itself, and holes must not overlap each other around the circle.
    """
    if plate_length_mm <= 0 or plate_width_mm <= 0 or plate_thickness_mm <= 0:
        raise ValueError("bolt_pattern_entities: plate dimensions must be positive")
    if hole_diameter_mm <= 0:
        raise ValueError("bolt_pattern_entities: hole_diameter_mm must be positive")
    if hole_count < 2:
        raise ValueError("bolt_pattern_entities: hole_count must be at least 2")
    if pattern_radius_mm <= 0:
        raise ValueError("bolt_pattern_entities: pattern_radius_mm must be positive")

    half_min_side = min(plate_length_mm, plate_width_mm) / 2.0
    if pattern_radius_mm + hole_diameter_mm / 2.0 >= half_min_side:
        raise ValueError(
            "bolt_pattern_entities: pattern_radius_mm + hole radius "
            f"({pattern_radius_mm + hole_diameter_mm / 2.0:.3g}mm) must leave real plate "
            f"material inside the plate's own half-extent ({half_min_side:.3g}mm)"
        )
    # Adjacent-hole arc spacing must exceed one hole diameter, or the
    # pattern would punch through itself.
    import math

    arc_spacing_mm = 2.0 * math.pi * pattern_radius_mm / hole_count
    if arc_spacing_mm <= hole_diameter_mm:
        raise ValueError(
            f"bolt_pattern_entities: {hole_count} holes on a {pattern_radius_mm:.3g}mm-radius "
            f"circle gives only {arc_spacing_mm:.3g}mm of arc spacing, less than the "
            f"{hole_diameter_mm:.3g}mm hole diameter -- holes would overlap"
        )

    half_length = plate_length_mm / 2.0
    half_width = plate_width_mm / 2.0
    hole_radius = hole_diameter_mm / 2.0

    return [
        {"id": "body1", "op": "create_body", "name": "bolt_pattern_plate"},
        {
            "id": "sk_plate",
            "op": "sketch",
            "body_ref": "body1",
            "plane": "XY",
            "elements": [
                {
                    "type": "rectangle",
                    "origin": [-half_length, -half_width],
                    "width": plate_length_mm,
                    "height": plate_width_mm,
                }
            ],
        },
        {
            "id": "plate",
            "op": "pad",
            "body_ref": "body1",
            "sketch_ref": "sk_plate",
            "depth": plate_thickness_mm,
        },
        {
            "id": "sk_hole",
            "op": "sketch",
            "body_ref": "body1",
            "plane": "XY",
            "elements": [
                {"type": "circle", "center": [pattern_radius_mm, 0.0], "radius": hole_radius}
            ],
        },
        {
            "id": "hole1",
            "op": "pocket",
            "body_ref": "body1",
            "sketch_ref": "sk_hole",
            # Through-hole: pocket the full plate thickness.
            "depth": plate_thickness_mm,
        },
        {
            "id": "holes",
            "op": "polar_pattern",
            "body_ref": "body1",
            "source_ref": "hole1",
            "axis": "Z",
            "count": hole_count,
            "angle": 360.0,
        },
    ]


def rib_entities(
    *,
    length_mm: float,
    height_mm: float,
    thickness_mm: float,
) -> list[dict[str, Any]]:
    """A thin triangular gusset rib: a right-triangle profile (base
    ``length_mm``, rise ``height_mm``) extruded by ``thickness_mm`` -- the
    common reinforcing feature for a thin-walled structural part (e.g. the
    real arm's own "Upper Arm Link", whose dominant sensitivity parameter
    this session's own FORGE-317/320 work already found to be wall
    thickness -- a rib is the standard alternative to thickening the whole
    wall when only local stiffness is needed).
    """
    if length_mm <= 0 or height_mm <= 0:
        raise ValueError("rib_entities: length_mm/height_mm must be positive")
    if thickness_mm <= 0:
        raise ValueError("rib_entities: thickness_mm must be positive")

    return [
        {"id": "body1", "op": "create_body", "name": "rib"},
        {
            "id": "sk_rib",
            "op": "sketch",
            "body_ref": "body1",
            "plane": "XZ",
            "elements": [
                {"type": "line", "start": [0.0, 0.0], "end": [length_mm, 0.0]},
                {"type": "line", "start": [length_mm, 0.0], "end": [0.0, height_mm]},
                {"type": "line", "start": [0.0, height_mm], "end": [0.0, 0.0]},
            ],
        },
        {
            "id": "rib_pad",
            "op": "pad",
            "body_ref": "body1",
            "sketch_ref": "sk_rib",
            "depth": thickness_mm,
            "midplane": True,
        },
    ]


#: feature_type -> macro function, keyed the same way
#: ``GenerateParametricFeatureInput.feature_type`` names them. Growing the
#: library is adding one entry here plus one new ``Literal`` value on that
#: schema -- never a new skill.
MACROS = {
    "bolt_pattern": bolt_pattern_entities,
    "rib": rib_entities,
}
