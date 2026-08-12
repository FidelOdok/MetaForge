# generate_cad

Generate parametric CAD geometry from a shape type and dimensions using CadQuery or FreeCAD.

## What it does

1. Takes a shape type, a dimensions map, and a material as input
2. Invokes the parametric CAD tool (CadQuery, falling back to FreeCAD) via MCP to build the solid
3. Exports the geometry to a STEP file
4. Unless `commit=False`, reads the STEP file back and calls `twin.commit_geometry`
   so a `cad_model` work product exists in the Twin without a separate step (MET-615)
5. Returns the file path plus computed mass properties (volume, surface area) and
   the commit outcome

## Tools Required

- `cadquery.create_parametric` -- primary parametric solid generation
- `freecad.create_parametric` -- fallback parametric solid generation
- `twin.commit_geometry` -- persistence (best-effort; see Limitations)

## Input

- `shape_type` -- primitive/parametric shape to generate (e.g. box, cylinder)
- `dimensions` -- shape-specific dimension map (mm)
- `material` -- material name for downstream analysis/BOM
- `output_path` -- destination path for the exported STEP file
- `constraints` -- optional dimensional constraints (min/max bounds)
- `project_id` -- optional project UUID to link the committed work product to
- `commit` -- persist into the Twin immediately (default `true`)

## Output

- `cad_file` -- path to the generated STEP file
- `shape_type` -- the shape that was generated
- `volume_mm3` -- solid volume in cubic millimeters
- `surface_area_mm2` -- surface area in square millimeters
- `parameters_used` -- the resolved parameters the solid was built from
- `material` -- material recorded on the part
- `committed` -- whether the geometry was persisted into the Twin
- `twin_node_id` / `model_url` -- set when `committed` is true
- `commit_error` -- set when `commit=true` was requested but persistence was
  skipped or failed (e.g. `twin.commit_geometry` unavailable, or the exported
  file isn't readable from this process — see Limitations)

## Limitations

- Generates a single parametric solid, not multi-body assemblies (see `create_assembly`)
- The commit step reads `cad_file` directly from disk, which only works when
  the CAD backend runs in-process with this skill (true for cadquery today).
  A containerized backend whose filesystem isn't shared with this process
  will report a non-fatal `commit_error` instead of a committed work product —
  check `committed` on the output rather than assuming success
- Bounded to the parametric shapes the CAD adapter exposes
