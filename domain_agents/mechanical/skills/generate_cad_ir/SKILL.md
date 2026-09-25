# generate_cad_ir

Generate CAD geometry from a Design IR document (requirements doc §6) by lowering it against one of two real compilers.

## What it does

1. Takes a list of Design IR entities (typed, id-addressed features: `create_body`, `sketch`, `pad`, `fillet_edges`, ...) as input
2. Validates them into a real `DesignIR` document (schema + referential integrity, `twin_core.design_ir`) before anything runs
3. Lowers the document via the selected `adapter`:
   - `"freecad"` (default) -- FreeCAD's session API, one MCP call per entity, via `domain_agents/shared/freecad_lowering.py` (the FreeCAD Lowering Pass, §6.6.2)
   - `"cadquery"` -- flattens the document into one generated script, executed via a single `cadquery.execute_script` call, via `domain_agents/shared/cadquery_lowering.py` (the CadQuery Lowering Pass, §6.6.2; narrower v1 op subset than FreeCAD's)
4. Measures and exports the document's terminal entity to STEP
5. Best-effort persists the result into the Twin, same as `generate_cad`

This is the structured path FR-1 describes (agents emit Design IR, never adapter-specific code), distinct from `generate_cad_script`'s sandboxed-script path (FR-13/NG2), which remains available for anything neither lowering pass covers.

## Tools Required

- `adapter="freecad"` (default): `freecad.open_session` / `freecad.close_session` -- session lifecycle, always both; `freecad.measure` / `freecad.export_model` -- every document needs a terminal measurement + export; whichever `freecad.*` authoring tools the document's entities use (`create_body`, `create_sketch`, `pad_sketch`, `fillet_edges`, ...)
- `adapter="cadquery"`: `cadquery.execute_script` -- the single call that compiles, executes, measures, and exports the whole document
- `twin.commit_geometry` -- persistence (best-effort), either adapter

## Input

- `name` -- **required.** Name for the generated part (e.g. `"Shoulder Yoke"`), used as the Twin work product's display name; must be specific, never a generic placeholder like the material alone (FORGE-97)
- `work_product_id` -- UUID of the CAD model work_product in the Digital Twin (optional)
- `entities` -- list of Design IR entities (requirements doc §6.2), typed as a real discriminated union on `op` (`twin_core.design_ir.IREntity`) -- the exact required fields per op are part of this skill's own MCP `input_schema`, not just prose here (FORGE-222); rejected with a clear error if malformed, not a crash
- `adapter` -- `"freecad"` (default, full v1 op coverage) or `"cadquery"` (real compiler, narrower v1 op subset -- see `cadquery_lowering.py`'s module docstring)
- `material` -- material name for metadata (default: aluminum_6061)
- `project_id` -- optional project UUID to link the committed work product to
- `commit` -- persist into the Twin immediately (default `true`)

## Output

- `cad_file` -- path to the exported STEP file
- `entity_count`, `volume_mm3`, `surface_area_mm2`, `bounding_box` -- of the terminal entity
- `obj_id_map` -- Design IR entity id -> the lowering pass's own per-entity handle, for diagnostics (FreeCAD `obj_id`, or CadQuery's generated script variable name)
- `committed` / `twin_node_id` / `model_url` / `commit_error` -- persistence outcome. When committed, the work product's metadata also carries `volume_mm3` / `surface_area_mm2` / `bbox_mm` / `mass_kg` (when `material` is given) as top-level canonical measured-property keys, the same convention `generate_cad`/`generate_enclosure`/`create_assembly` use (FORGE-100), so a constraint expression can read a real measured value.

## Limitations (v1 -- see each lowering module's own docstring for the authoritative list)

- `adapter="freecad"`: no `create_parametric` (legacy file-based tool, no session equivalent); no assembly/multi-body export; no `rotation` on `transform`, no `orientation` on `place`; no checkpoint cache or incremental re-lowering (§6.6.3); boolean takes exactly one `tool_ref` per entity
- `adapter="cadquery"`: same `create_parametric`/assembly/rotation/checkpoint-cache/single-`tool_ref` cuts as FreeCAD, plus a narrower op subset -- only `create_primitive` (box/cylinder/sphere), `transform`, `boolean`, `sketch` (rectangle/circle/line, not arc), and `pad`/`pocket` are implemented; everything else raises a clear error rather than guessing
- Either adapter: the document's terminal entity must be a single exportable solid, and editing means resubmitting the full entity list
