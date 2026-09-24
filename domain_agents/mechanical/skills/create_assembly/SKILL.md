# create_assembly

Create multi-part CAD assemblies with positioning and mating constraints using CadQuery.

## What it does

1. Takes a list of parts (each referenced by Twin `node_id` or a raw STEP file path)
   with positions and optional constraints
2. Materializes any `node_id`-referenced part via `twin.stage_work_product_file`
3. Invokes the `cadquery.create_assembly` MCP tool to combine parts
4. Applies mating constraints and solves assembly positions if constraints provided
5. Persists the result into the Twin via `twin.commit_geometry` (unless `commit=false`)
6. Returns the assembly STEP file, part count, total volume, interference check, and commit result

## Tools Required

- `cadquery.create_assembly` -- CadQuery multi-part assembly creation
- `twin.stage_work_product_file` -- materializes a `node_id`-referenced part onto the shared
  adapter workspace (only needed when a part uses `node_id`)
- `twin.commit_geometry` -- persistence (best-effort; failure is reported on the output, not raised)

## Input

- `work_product_id` -- UUID of an existing assembly work_product to link to (optional --
  `commit_geometry` creates a fresh `CAD_MODEL` work product when omitted, same as `generate_cad`)
- `parts` -- List of parts (name, plus exactly one of: `node_id` -- the Twin node of an
  already-committed part, the preferred and durable reference -- or `file`, a raw STEP path
  already on the shared adapter workspace; optional location x/y/z/rx/ry/rz)
- `constraints` -- Optional assembly constraints (part_a, part_b, type: Point/Axis/Plane)
- `output_path` -- Optional output STEP file path
- `material` -- Material name for metadata (default: ABS)
- `project_id` -- Project UUID to link the resulting work product to, when committed
- `commit` -- Persist into the Twin immediately (default: true)

## Output

- `assembly_file` -- Path to the generated assembly STEP file
- `part_count` -- Number of parts in the assembly
- `total_volume` -- Total volume of all parts in mm^3
- `interference_check_passed` -- Whether parts don't collide
- `committed` -- Whether the assembly was persisted into the Twin
- `twin_node_id` -- Twin node ID of the committed `cad_model`, when committed
- `model_url` -- Viewer URL of the committed `cad_model`, when committed
- `commit_error` -- Set when `commit=true` was requested but persistence was skipped or failed

## Limitations

- Constraint types limited to Point, Axis, Plane, PointInPlane
- Interference check is basic (not full collision detection)
- Phase 2 skill
