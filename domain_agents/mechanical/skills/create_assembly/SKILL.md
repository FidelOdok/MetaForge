# create_assembly

Create multi-part CAD assemblies with positioning and mating constraints using CadQuery.

## What it does

1. Takes a list of STEP file parts with positions and optional constraints
2. Invokes the `cadquery.create_assembly` MCP tool to combine parts
3. Applies mating constraints and solves assembly positions if constraints provided
4. Returns the assembly STEP file, part count, total volume, and interference check result

## Tools Required

- `cadquery.create_assembly` -- CadQuery multi-part assembly creation

## Input

- `work_product_id` -- UUID of the assembly work_product in the Digital Twin
- `parts` -- List of parts (name, STEP file path, optional location x/y/z/rx/ry/rz)
- `constraints` -- Optional assembly constraints (part_a, part_b, type: Point/Axis/Plane)
- `output_path` -- Optional output STEP file path

## Output

- `assembly_file` -- Path to the generated assembly STEP file
- `part_count` -- Number of parts in the assembly
- `total_volume` -- Total volume of all parts in mm^3
- `interference_check_passed` -- Whether parts don't collide

## Limitations

- Constraint types limited to Point, Axis, Plane, PointInPlane
- Interference check is basic (not full collision detection)
- Phase 2 skill
