# run_cfd

Answer a flow or thermal question with what MetaForge can solve: steady incompressible internal flow through a bounded domain (OpenFOAM, FORGE-557), or steady conduction through a part to a fixed-temperature sink (CalculiX). One analysis per call.

## What it does

1. `internal_flow`: invokes `openfoam.run_internal_flow` with the fluid, the inlet speed, the turbulence model and the inlet, outlet and wall STL surfaces. It returns the static pressure drop inlet to outlet, the flow rate, the mesh size and whether the solver converged.
2. `conduction`: invokes `calculix.run_thermal` with a volume mesh, a material, a heat-source face with its dissipation and a sink face with its temperature. It returns the peak and minimum temperature, labelled `conduction_only`.

It records nothing itself. A flow run that fails raises with the stage and OpenFOAM's own reason; one that does not converge says so in `warnings`. Free-form `fluid_properties` and `boundary_conditions`, a call with no analysis, and a call with both analyses are refused.

## Tools Required

- `openfoam.run_internal_flow` -- steady internal flow (needs the `openfoam-adapter` container)
- `calculix.run_thermal` -- steady conduction solve

## Input

- `work_product_id` -- twin work_product id for the mechanical design
- `internal_flow` -- `inlet_stl`, `outlet_stl`, `wall_stls`, `stl_units` (m or mm), `inside_point` (a point in the fluid, in those units), `fluid` (`density_kg_m3`, `kinematic_viscosity_m2_s`), `inlet_velocity_m_s`, `turbulence` (`laminar` or `kOmegaSST` with `turbulence_intensity` and `turbulence_length_scale_m`), optional `mesh` and `solver`
- `conduction` -- `mesh_file`, `material` (`name` or `thermal_conductivity_w_mk`), `heat_source_node_set`, `power_dissipation_w`, `sink_node_set`, `sink_temp_c`
- `geometry_file` -- optional, the geometry the case came from, for the record

## Output

- `analysis` -- `internal_flow` or `conduction_only`
- Flow: `pressure_drop_pa`, `volumetric_flow_m3_s`, `converged`, `solution_stable`, `mesh_cells`, `case_dir`
- Conduction: `max_temperature_c`, `min_temperature_c`
- `max_velocity_ms` -- always null: not computed
- `solver`, `warnings`

## Limitations

- Flow is steady, incompressible, single phase and constant property, with a uniform inlet speed and a zero-gauge-pressure outlet. No heat transfer in the fluid, no layer meshing, no rotating machinery, no compressible flow, no external flow
- The surfaces must be the *fluid* (the inside of the duct), as closed STL on the adapter workspace. MetaForge does not yet derive them from a committed solid
- The laminar path is checked against a straight pipe with a closed-form answer. The k-omega SST path has not been validated against measurements: treat it as a screening estimate
- Conduction has no convection to air, so a part cooled mainly by airflow comes out hotter than it is only if the sink face really is its main path; say which assumption holds
