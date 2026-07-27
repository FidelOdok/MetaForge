# generate_cad

Generate parametric CAD geometry from a shape type and dimensions using CadQuery or FreeCAD.

## What it does

1. Takes a shape type, a dimensions map, and a material as input
2. Invokes the parametric CAD tool (CadQuery, falling back to FreeCAD) via MCP to build the solid
3. Exports the geometry to a STEP file
4. Returns the file path plus computed mass properties (volume, surface area)

## Tools Required

- `cadquery.create_parametric` -- primary parametric solid generation
- `freecad.create_parametric` -- fallback parametric solid generation

## Input

- `shape_type` -- primitive/parametric shape to generate (e.g. box, cylinder)
- `dimensions` -- shape-specific dimension map (mm)
- `material` -- material name for downstream analysis/BOM
- `output_path` -- destination path for the exported STEP file
- `constraints` -- optional dimensional constraints (min/max bounds)

## Output

- `cad_file` -- path to the generated STEP file
- `shape_type` -- the shape that was generated
- `volume_mm3` -- solid volume in cubic millimeters
- `surface_area_mm2` -- surface area in square millimeters
- `parameters_used` -- the resolved parameters the solid was built from
- `material` -- material recorded on the part

## Limitations

- Generates a single parametric solid, not multi-body assemblies (see `create_assembly`)
- The STEP export is written to the adapter's filesystem; persisting it as a
  viewable `cad_model` in the twin requires the commit-geometry step
- Bounded to the parametric shapes the CAD adapter exposes
