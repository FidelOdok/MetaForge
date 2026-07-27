# run_cfd

Run a computational fluid dynamics analysis using the CalculiX thermal/flow solver.

## What it does

1. Takes a mechanical design work_product and the flow boundary conditions
2. Invokes the CalculiX thermal solver via MCP to solve the flow/thermal field
3. Extracts the key results and records them against the work_product in the twin

## Tools Required

- `calculix.run_thermal` -- CalculiX thermal/flow solve

## Input

- `work_product_id` -- twin work_product id for the mechanical design
- flow/thermal boundary conditions

## Output

- `work_product_id` -- the design the result is attached to
- `max_velocity_ms` -- maximum fluid velocity (m/s)
- `pressure_drop_pa` -- pressure drop across the domain (Pa)

## Limitations

- Uses the CalculiX thermal solver as a flow proxy -- not a general-purpose CFD code
- Steady-state; no transient/turbulence-model selection
