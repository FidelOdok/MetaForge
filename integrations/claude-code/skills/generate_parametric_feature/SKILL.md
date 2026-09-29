---
name: generate_parametric_feature
description: Generate a named, reusable parametric feature from the feature library (FORGE-269, gap G-D1) -- bolt_pattern (a mounting plate with a circular bolt-hole pattern) or rib (a triangular gusset rib), each a typed-parameter macro over Design IR entities, lowered and committed via the same path generate_cad_ir uses. Growing the library is adding one macro function, not a new skill -- more feature_type values may appear over time.
tools: [cadquery.execute_script, freecad.close_session, freecad.create_body, freecad.create_sketch, freecad.export_model, freecad.measure, freecad.open_session, freecad.pad_sketch, freecad.pocket_sketch, freecad.polar_pattern, twin.commit_geometry]
domain: mechanical
---

# generate_parametric_feature

Generate a named, reusable parametric feature from the feature library (FORGE-269, gap G-D1) -- a typed-parameter macro over Design IR entities, not a hand-authored entity list.

## What it does

1. Takes a `feature` (a discriminated union on `feature_type`: `bolt_pattern` or `rib`, each with its own typed parameters) as input
2. Builds the feature's Design IR entity sequence via the matching macro function in `domain_agents/shared/design_ir_macros.py` -- pure composition of entities `generate_cad_ir` already supports (`create_body`/`sketch`/`pad`/`pocket`/`polar_pattern`), no new compiler work
3. Delegates lowering, measurement, export, and Twin commit to `generate_cad_ir`'s own handler directly -- zero duplication of that skill's real compiler/commit logic

Growing the library (`bearing_seat`, `motor_mount`, `clevis_yoke`, `gear_stage`, `cable_channel` -- the ticket's remaining named features, deliberately deferred: see `design_ir_macros.py`'s own module docstring for why each needs more than this first cut) means adding one macro function plus one `Literal` value on `feature.feature_type` -- never a new skill.

## Tools Required

Same as `generate_cad_ir` (this skill composes it directly): FreeCAD session lifecycle + authoring tools for `adapter="freecad"` (default), `cadquery.execute_script` for `adapter="cadquery"` (note: `bolt_pattern` uses `polar_pattern`, unsupported by the CadQuery Lowering Pass -- use the default `freecad` adapter for it), `twin.commit_geometry` for persistence.

## Input

- `name` -- **required.** Name for the generated part.
- `feature` -- **required.** `{"feature_type": "bolt_pattern", "plate_length_mm": ..., "plate_width_mm": ..., "plate_thickness_mm": ..., "hole_diameter_mm": ..., "hole_count": ..., "pattern_radius_mm": ...}` or `{"feature_type": "rib", "length_mm": ..., "height_mm": ..., "thickness_mm": ...}`. `bolt_pattern`'s macro rejects a `pattern_radius_mm` that would leave holes overlapping or running outside the plate -- a structural check, not just a plausible-sounding one.
- `work_product_id` -- optional (new generation vs. existing)
- `adapter` -- `"freecad"` (default) or `"cadquery"`
- `material` -- default `aluminum_6061`
- `project_id` -- optional
- `commit` -- default `true`

## Output

Same shape as `generate_cad_ir`'s output, plus `feature_type` naming which macro ran: `cad_file`, `entity_count`, `volume_mm3`, `surface_area_mm2`, `bounding_box`, `committed`/`twin_node_id`/`model_url`/`commit_error`/`already_committed`.

## Limitations (v1)

- Generates a **standalone** feature as its own new CAD_MODEL work product -- does NOT modify an existing committed part's geometry in place (that is FORGE-270's own gap, "editable parameters on committed parts", a separate ticket).
- Only `bolt_pattern` and `rib` today -- see this module's own docstring and `design_ir_macros.py`'s for the honest reasoning behind deferring the other five named features from the ticket's own Jira wording.
