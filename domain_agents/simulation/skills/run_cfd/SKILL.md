# run_cfd

Answer the part of a thermal question MetaForge can solve today: steady conduction through a part to a fixed-temperature sink. MetaForge has no flow solver (FORGE-543).

## What it does

1. Takes a conduction case: a volume mesh, a material, the heat-source face with its dissipation, and the sink face with its temperature
2. Invokes `calculix.run_thermal` with exactly those arguments (steady state)
3. Returns the peak and minimum temperature, labelled `conduction_only`; it records nothing itself

A request carrying flow inputs (`fluid_properties`, `boundary_conditions`) is refused with the reason: velocity, pressure drop and convection need a flow solver, and returning zeros for them would read as an answer.

## Tools Required

- `calculix.run_thermal` -- steady conduction solve

## Input

- `work_product_id` -- twin work_product id for the mechanical design
- `conduction` -- `mesh_file`, `material` (`name` or `thermal_conductivity_w_mk`), `heat_source_node_set`, `power_dissipation_w`, `sink_node_set`, `sink_temp_c`
- `geometry_file` -- optional, the geometry the case came from, for the record

## Output

- `analysis` -- always `conduction_only`
- `max_temperature_c`, `min_temperature_c`
- `max_velocity_ms`, `pressure_drop_pa` -- always null: not computed
- `warnings` -- includes "conduction only: no convection to air and no flow field"

## Limitations

- No convection to air: a part cooled mainly by airflow comes out hotter than it is only if the sink face really is its main path; say which assumption holds
- Flow solver decision (FORGE-543): OpenFOAM in its own adapter container (`foamRun` incompressible and buoyant solvers, `snappyHexMesh` meshing), built on CI runners rather than a shared dev box. Until it lands, flow questions are refused
