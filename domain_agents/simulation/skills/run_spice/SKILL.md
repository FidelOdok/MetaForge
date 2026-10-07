# run_spice

Run a SPICE circuit simulation with ngspice (operating point, DC sweep, AC, or transient).

## What it does

1. Takes a netlist (text, or a path on the adapter workspace such as `kicad.export_netlist` output), an analysis type and its parameters
2. Invokes `spice.run_simulation`, which runs ngspice in the spice-adapter container (FORGE-542)
3. Returns the solved vectors and whether the run converged; it records nothing itself

## Tools Required

- `spice.run_simulation` -- ngspice op/dc/ac/transient solve

## Input

- `work_product_id` -- twin work_product id for the circuit design
- `netlist` or `netlist_path` -- exactly one
- `analysis_type` -- `op`, `dc`, `ac`, or `transient`
- `params` -- dc: source/start/stop/step; ac: variation/points/fstart/fstop; transient: step/stop/start/max_step
- `probes` -- vectors to return (default all)

## Output

- `results` -- per vector final/min/max with unit (AC: magnitude in dB and phase)
- `waveform_data`, `scale` -- decimated waveforms keyed with the sweep variable
- `waveforms` -- rawfile paths on the adapter; `convergence`; `sim_time_s`; `log` when it failed

## Limitations

- ngspice models only; models must be in the netlist or on the adapter workspace
- Non-convergence is reported with ngspice's errors, not auto-remediated
