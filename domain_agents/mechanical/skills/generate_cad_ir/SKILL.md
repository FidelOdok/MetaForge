# generate_cad_ir

Generate CAD geometry from a Design IR document (requirements doc §6) by lowering it against FreeCAD's session API.

## What it does

1. Takes a list of Design IR entities (typed, id-addressed features: `create_body`, `sketch`, `pad`, `fillet_edges`, ...) as input
2. Validates them into a real `DesignIR` document (schema + referential integrity, `twin_core.design_ir`) before anything runs
3. Lowers the document against FreeCAD's session API, one MCP call per entity, via `domain_agents/shared/freecad_lowering.py` (the FreeCAD Lowering Pass, §6.6.2)
4. Measures and exports the document's terminal entity to STEP
5. Best-effort persists the result into the Twin, same as `generate_cad`

This is the structured path FR-1 describes (agents emit Design IR, never adapter-specific code), distinct from `generate_cad_script`'s sandboxed-script path (FR-13/NG2), which remains available for anything this skill doesn't cover.

## Tools Required

- `freecad.open_session` / `freecad.close_session` -- session lifecycle, always both
- `freecad.measure` / `freecad.export_model` -- every document needs a terminal measurement + export
- Whichever `freecad.*` authoring tools the document's entities use (`create_body`, `create_sketch`, `pad_sketch`, `fillet_edges`, ...)
- `twin.commit_geometry` -- persistence (best-effort)

## Input

- `work_product_id` -- UUID of the CAD model work_product in the Digital Twin (optional)
- `entities` -- list of Design IR entity dicts (requirements doc §6.2); rejected with a clear error if malformed, not a crash
- `material` -- material name for metadata (default: aluminum_6061)
- `project_id` -- optional project UUID to link the committed work product to
- `commit` -- persist into the Twin immediately (default `true`)

## Output

- `cad_file` -- path to the exported STEP file
- `entity_count`, `volume_mm3`, `surface_area_mm2`, `bounding_box` -- of the terminal entity
- `obj_id_map` -- Design IR entity id -> FreeCAD `obj_id`, for diagnostics
- `committed` / `twin_node_id` / `model_url` / `commit_error` -- persistence outcome

## Limitations (v1 -- see `freecad_lowering.py`'s module docstring for the authoritative list)

- FreeCAD only -- no CadQuery Lowering Pass yet (that side is a real compiler, separate work)
- No `create_parametric` (legacy file-based tool, no session equivalent) -- rejected with a clear error
- No assembly/multi-body export -- the document's terminal entity must be a single exportable solid
- No `rotation` on `transform`, no `orientation` on `place` -- the real FreeCAD tools don't support them
- No checkpoint cache or incremental re-lowering (§6.6.3) -- editing means resubmitting the full entity list
- Boolean takes exactly one `tool_ref` per entity -- chained booleans need multiple `boolean` entities
