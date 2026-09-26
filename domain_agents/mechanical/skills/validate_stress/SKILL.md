# validate_stress

Validates stress analysis results against design constraints using CalculiX FEA.

## What it does

1. Takes a CAD model work_product ID and stress constraints as input
2. Invokes CalculiX FEA solver via MCP to run static stress analysis
3. Compares computed stress values against allowable limits (with safety factors)
4. Returns pass/fail per region with detailed stress results

## Tools Required

- `calculix.run_fea` -- CalculiX static stress analysis

## Input

- `work_product_id` -- UUID of the CAD model work_product in the Digital Twin
- `mesh_file_path` -- Path to the .inp mesh file
- `load_case` -- Load case identifier
- `constraints` -- List of stress constraints (max_von_mises_mpa, safety_factor, material)
- `material_name` -- Material name for FEA elastic properties (e.g. `"steel"`, `"aluminum_6061"`)
- `fixed_node_set` -- Mesh element set name to fully constrain (fixed support)
- `load_node_set` -- Mesh element set name to apply `load_force_n` to
- `load_force_n` -- `[Fx, Fy, Fz]` total applied force in Newtons

## Output

- `overall_passed` -- Whether all constraints were satisfied
- `results` -- Per-region stress results with actual vs. allowable values
- `max_stress_mpa` -- Global maximum stress found
- `critical_region` -- Region with highest stress
- `solver_time_seconds` -- FEA solver execution time
- `mesh_elements` -- Number of mesh elements used

## Limitations

- Currently only supports static stress analysis (no modal, thermal)
- Safety factor is applied as simple division (max_allowable / safety_factor)
- Does not account for fatigue or cyclic loading
