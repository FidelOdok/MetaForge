# generate_parametric_feature

Generate a named, reusable parametric feature from the feature library (FORGE-269, gap G-D1) -- a typed-parameter macro over Design IR entities, not a hand-authored entity list.

## What it does

1. Takes a `feature` (a discriminated union on `feature_type`: `bolt_pattern` or `rib`, each with its own typed parameters) as input
2. Builds the feature's Design IR entity sequence via the matching macro function in `domain_agents/shared/design_ir_macros.py` -- pure composition of entities `generate_cad_ir` already supports (`create_body`/`sketch`/`pad`/`pocket`/`polar_pattern`), no new compiler work
3. Delegates lowering, measurement, export, and Twin commit to `generate_cad_ir`'s own handler directly -- zero duplication of that skill's real compiler/commit logic, threading the macro's own kwargs (plus `feature_type`) through as `generate_cad_ir`'s `parameters` (FORGE-270, gap G-D2) so the committed node records what value produced it

**Editing a feature (FORGE-270)**: call this skill again with the SAME `name`, `project_id`, and a CHANGED parameter value. The existing same-name `SUPERSEDES` matching in `api_gateway/twin/geometry_recorder.py` links the new node to the one it replaces automatically -- no separate "edit" action. `GET /v1/features/{work_product_id}/diff` then reports which parameters changed between the two versions.

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

- Generates a **standalone** feature as its own new CAD_MODEL work product -- never modifies an existing committed part's geometry in place. Re-running with the SAME `name`+`project_id` produces a new, `SUPERSEDES`-linked version (see "Editing a feature" above) -- that is the closest thing to "in place" this skill offers, deliberately, matching how every other re-commit in this codebase already versions.
- `SUPERSEDES` linking (and therefore the diff route) requires `project_id` -- unscoped commits have no reliable identity to match a predecessor on and are never linked (same rule every other `geometry_recorder.py` caller already follows).
- Only `bolt_pattern` and `rib` today -- see this module's own docstring and `design_ir_macros.py`'s for the honest reasoning behind deferring the other five named features from the ticket's own Jira wording.
