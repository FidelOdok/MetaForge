# run_fea

Run a finite element analysis using the CalculiX solver.

## What it does

1. Takes a mechanical design work_product and its FEA mesh
2. Invokes the CalculiX solver via MCP to run static structural analysis
3. Extracts peak stress/displacement and records them against the work_product in the twin

## Tools Required

- `calculix.run_fea` -- CalculiX static structural solve

## Input

- `work_product_id` -- twin work_product id for the mechanical design
- `mesh_file` -- path to the FEA mesh (.inp/.unv)

## Output

- `work_product_id` -- the design the result is attached to
- `max_stress_mpa` -- maximum von Mises stress (MPa)
- `max_displacement_mm` -- maximum displacement (mm)
- `solver_time_s` -- solver wall-clock time

## Limitations

- Static structural analysis only (no modal/thermal/nonlinear)
- Result quality depends on the supplied mesh; meshing is `generate_mesh`
