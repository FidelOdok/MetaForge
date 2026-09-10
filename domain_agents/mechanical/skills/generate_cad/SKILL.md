# generate_cad

Generate parametric CAD geometry from a shape type and dimensions using CadQuery or FreeCAD.

## What it does

1. Takes a shape type, a dimensions map, and a material as input
2. Invokes the parametric CAD tool (CadQuery, falling back to FreeCAD) via MCP to build the solid
3. Exports the geometry to a STEP file, then (unless `commit=False`) commits it via
   `twin.commit_geometry` so a `cad_model` work product exists in one step (MET-615)
4. Returns the file path, mass properties (volume, surface area), and commit outcome

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

- `cad_file`, `shape_type`, `volume_mm3`, `surface_area_mm2`, `parameters_used`,
  `material` -- as before
- `committed` / `twin_node_id` / `model_url` -- persistence outcome
- `commit_error` -- set when a requested commit was skipped or failed

## Limitations

- Single parametric solid, not multi-body assemblies (see `create_assembly`)
- Commit reads `cad_file` from disk, which only works when the CAD backend runs
  in-process with this skill (true for cadquery today); a containerized backend
  reports `commit_error` instead -- check `committed`, don't assume success
- Bounded to the parametric shapes the CAD adapter exposes
