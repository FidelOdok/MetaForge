# run_spice

Run a SPICE circuit simulation (DC, AC, or transient).

## What it does

1. Takes a circuit design work_product, an analysis type, and analysis parameters
2. Invokes the SPICE engine via MCP to run the requested analysis
3. Records convergence and the extracted results against the work_product in the twin

## Tools Required

- `spice.run_simulation` -- SPICE DC/AC/transient solve

## Input

- `work_product_id` -- twin work_product id for the circuit design
- `analysis_type` -- `dc`, `ac`, or `transient`
- `params` -- analysis-specific parameters

## Output

- `work_product_id` -- the circuit the result is attached to
- `convergence` -- whether the simulation converged
- extracted analysis results + `sim_time_s`

## Limitations

- Requires a valid SPICE netlist on the work_product
- Non-convergence is reported, not auto-remediated
