---
description: Answer a flow or cooling question about a part honestly. Runs a steady incompressible internal flow with OpenFOAM when the fluid is bounded by an inlet, an outlet and walls (pressure drop and flow rate), runs the steady conduction model when the question is heat conducted to a fixed-temperature sink, and otherwise records the question, the assumptions and any results the user brings from their own CFD tool. Use when the user asks for CFD, airflow, pressure drop, flow velocity, duct or channel sizing, convection cooling, or "will this part stay below N degrees", or when a requirement needs flow or thermal evidence.
---

# run_cfd

Answer a flow or cooling question about a real part without overstating what
MetaForge can compute. The server has two solvers that bear on it:

- **Steady internal flow** (`openfoam.run_internal_flow`, OpenFOAM 11): steady,
  incompressible, single-phase flow through a domain bounded by an inlet, an
  outlet and walls, laminar or k-omega SST. It returns the static pressure drop
  inlet to outlet, the flow rate and the mesh quality.
- **Steady conduction** (`calculix.run_thermal`): a heat source on one face
  conducting through the part to a face held at a fixed temperature.

It has **no** solver for convective heat transfer, external flow (a body in a
stream), rotating parts such as a fan rotor, compressible flow or multiphase
flow. Decide with the user which question they actually have, run the solver
that fits, and record everything else as an open question rather than a number.

## When to use it

- "What pressure drop does this duct give at 2 m/s?" (internal flow: can be
  run, if the fluid volume is bounded and closed)
- "The regulator dissipates 1.5 W into the bracket, which is bolted to a
  chassis at 40 C. How hot does the regulator pad get?" (conduction: can be
  run)
- "Is a 40 mm fan enough to cool the enclosure?" (convection and a fan: not
  solved here)
- "How much drag does this body have in a 10 m/s stream?" (external flow: not
  solved here)
- A requirement on temperature, airflow or pressure drop needs evidence.

Not for: structural stress or frequency (use `run_fea`), joint loads
(`calculix.compute_joint_loads`), or a part with no committed geometry yet
(design it first with `generate_cad`).

## Tools and profile

| Step | Tool | Profile |
|---|---|---|
| Get the CAD file | `twin.stage_work_product_file` | `simulation` |
| Mesh it, read faces | `freecad.generate_mesh`, `freecad.list_named_faces` | `simulation` |
| Check the mesh | `calculix.validate_mesh` | `simulation` |
| Conduction solve | `calculix.run_thermal` | `simulation` |
| Internal flow solve | `openfoam.run_internal_flow` | `simulation` |
| Re-read results | `calculix.extract_results` | `simulation` |
| Hand-calc cross-check | `calculix.cross_check_thermal_steady_state` | no-profile connection only |
| Record result or question | `twin.record_document` | `core` |
| Record modelling choices | `twin.record_decision` | every profile |

The solve runs on **`simulation`** and the result document is written on
**`core`**. If one connection does not serve both, solve on `simulation`,
keep the outputs, and record on a `core` connection; tell the user which
profile to use for each. If a tool is missing, check `health.check` ->
`profile` before assuming anything is broken. `openfoam.run_internal_flow`
needs the `openfoam-adapter` container, which runs only when the host has the
`cfd` compose profile on; if a call says the adapter is not running, tell the
user, do not look for a workaround, and do not present `calculix.run_thermal`
output as a flow result.

## Inputs you need before you start

Ask the user for anything missing. Do not fill these in with defaults.

| Input | Why it matters | Example |
|---|---|---|
| The question in physical terms | Decides flow, convection or conduction | "regulator pad temperature" vs "pressure drop at 2 m/s" |
| The part | Its twin node id, or its name in the project | `PSU Bracket` |
| Material | Thermal conductivity drives the conduction answer | `aluminium_6061_t6`, or `{"thermal_conductivity_w_mk": 167}` |
| Heat source face and power | Where the heat enters, total watts | "the regulator pad, 1.5 W" |
| Where the power figure came from | A wattage with no source is not evidence | the component's datasheet, a measurement |
| Sink face and its temperature | Where the heat leaves, held at a fixed temperature | "the two mounting tabs, 40 C" |
| Acceptance limit | What counts as a pass | "pad <= 85 C", "pressure drop <= 20 Pa" |

For an **internal flow**, also ask for:

| Input | Why it matters | Example |
|---|---|---|
| The fluid, with its density and kinematic viscosity | The answer scales with both; no default is safe | air at 20 C: 1.204 kg/m^3 and 1.516e-5 m^2/s, from a cited table |
| The inlet speed, uniform and normal to the inlet | Sets the flow rate | "2 m/s" |
| Laminar or turbulent | Chooses the model; Reynolds number decides | Re = v D / nu; below about 2300 in a pipe is laminar |
| For turbulent: intensity and length scale at the inlet | They set the inlet k and omega, nothing is assumed | intensity 0.05, length scale 0.07 of the hydraulic diameter |
| The fluid-domain surfaces: inlet, outlet, walls, as STL, and their units | The solver meshes the fluid, not the solid | `duct/inlet.stl`, `duct/outlet.stl`, `duct/wall.stl`, in mm |
| A point inside the fluid, in the STL units | Tells the mesher which side is fluid | the middle of the duct |

For convection, external flow or anything else not solved, ask for the fluid,
inlet conditions, outlet conditions, the heat loads and the limit. You will
record these, not solve them.

## Procedure

### 1. Classify the question with the user

State back which physics the question needs:

- **Conduction to a fixed-temperature sink**, no air movement modelled: go to
  step 2.
- **Internal flow** (pressure drop or flow rate through a duct, channel or pipe
  whose fluid volume you can bound and close): go to step 6.
- **Convection** (cooling by air or liquid, fan sizing, natural convection to
  ambient), **external flow**, **a rotating part**, **compressible flow**:
  go to step 7. Say plainly that MetaForge cannot solve this.

If a conduction model could bound a convection question (for example, the
worst case with all heat leaving through the mounting face), offer it as a
bound and let the user decide. Never present a bound as the answer.

### 2. Stage and mesh

1. Find the part's CAD work product from `metaforge://twin/brief/<project_id>`
   or with `twin.find_by_property` / `twin.get_node`. Note its node id and
   revision.
2. Call `twin.stage_work_product_file` with `node_id`; keep the `file_path`.
3. Call `freecad.generate_mesh` with `input_file` = that path,
   `output_format: "inp"`, and an `element_size` about a tenth of the part's
   smallest important dimension.
4. Read the `faces` table. Pick the heat source face and the sink face by
   bounding box, centroid and normal, not by the `SurfaceN` name. Say which
   group you chose and why. `freecad.list_named_faces` re-reads the table
   for a mesh you already have.
5. Call `calculix.validate_mesh` on the `mesh_file`; fix the mesh first if
   it fails.

### 3. Solve

Call `calculix.run_thermal` with:

- `mesh_file`: from step 2
- `material`: `{"name": "aluminium_6061_t6"}` or
  `{"thermal_conductivity_w_mk": 167}`. **W/(m*K), SI**: the server converts
  to the mesh's millimetre units itself.
- `heat_source_node_set`: the source face group
- `power_dissipation_w`: the user's **total** watts, spread over that face
- `sink_node_set`: the sink face group
- `sink_temp_c`: the user's sink temperature
- `analysis_mode`: `steady_state` (the only implemented mode;
  `transient` is refused)

Keep `max_temperature_c`, `min_temperature_c`, `frd_path`, the `file` value inside `field` and
any `warnings`.

### 4. Decide whether the number can be trusted

1. **Sanity.** `min_temperature_c` should equal the sink temperature, and
   `max_temperature_c` should be above it. Anything else means the faces or
   the power are wrong.
2. **Mesh sensitivity.** Re-mesh at about half the element size, solve again
   with identical inputs, and compare the two `max_temperature_c` values
   yourself. `calculix.check_mesh_convergence` takes stress values only, so
   do not pass temperatures to it. Report the change in percent; if it is
   more than a few percent, refine again.
3. **Hand calculation.** If `calculix.cross_check_thermal_steady_state` is
   in your tool list and the heat path is a simple bar (one conduction
   length, one cross-section), call it with `conduction_length_mm`,
   `cross_section_area_mm2`, `thermal_conductivity_w_mk`,
   `power_dissipation_w`, `sink_temp_c` and `fea_peak_temp_c`. Take the
   geometry numbers from the user or from `cadquery.get_properties`, not
   from estimation. If the tool is not listed or the geometry is not a bar,
   say no closed-form check was done.

### 5. Record the conduction result

On a `core` connection, call `twin.record_document` with
`document_type: "simulation_result"`, a `name` naming part, case and
revision, a `content` summary (material, source and sink, power, both mesh
sizes, peak temperature, the sensitivity result, the cross-check verdict,
and the sentence "conduction only, no convection or flow modelled"),
`analysed_geometry_node_id`, `analysed_geometry_revision`, `field_file` (the
`file` value inside the solver's `field`), and
`load_case_spec` with the source, sink, power and sink temperature. Then go
to step 8.

### 6. Internal flow: run OpenFOAM

This solves steady, incompressible flow through a domain bounded by an inlet,
an outlet and walls. It is checked against one case with a closed-form answer
(see Limits); everything else is the solver doing its job on your inputs.

1. **Get the fluid-domain surfaces.** The solver needs the *fluid* (the inside
   of the duct or channel), not the solid part, as STL surfaces on the adapter
   workspace: one for the **inlet**, one for the **outlet** and one or more for
   the **walls**, which together close around the fluid. MetaForge does not yet
   derive these from a committed solid. Build the fluid volume as its own CAD
   model with the CAD tools your connection lists, and export the inlet face,
   the outlet face and the wall faces as separate STL files. If you cannot make
   closed surfaces, say so and go to step 7 rather than guessing.
2. **Check the units and the inside point.** Say whether the STL is in metres
   or millimetres; `inside_point` is in the same units and must be inside the
   fluid, not in the wall.
3. Call `openfoam.run_internal_flow` with `inlet_stl`, `outlet_stl`,
   `wall_stls`, `stl_units`, `inside_point`, `fluid`
   (`density_kg_m3`, `kinematic_viscosity_m2_s`), `inlet_velocity_m_s` and
   `turbulence` (`laminar`, or `kOmegaSST` with `turbulence_intensity` and
   `turbulence_length_scale_m`). Leave `mesh` and `solver` out for a first run;
   the result reports what it used (`plan`). A run takes seconds for tens of
   thousands of cells and longer as the mesh grows; the adapter runs one job at
   a time.
4. **Read the result.**
   - `completed: false`: the run stopped at `stage_failed` with a reason in
     `error` and a log tail. A leak, a wrong `inside_point` and an unreadable
     surface all show up here. Fix the input; do not retry the same call.
   - `converged`: the solver's residual controls were met. `false` is not
     automatically wrong. In straight-pipe flow the transverse velocity
     residuals stall near 0.1 because those components are almost zero.
     `solution_stable` and `pressure_drop_drift` say whether the pressure drop
     had stopped moving over the last tenth of the run.
   - `pressure_drop_pa`: the static pressure drop, inlet to outlet, in Pa.
   - `inlet` and `outlet`: area, flow rate, mass flow, mean velocity. Both flows
     are positive in the direction of travel; `flow_balance_error` should be
     tiny.
   - `mesh`: cell count, non-orthogonality, skewness, `mesh_ok`.
   - `y_plus` (turbulent only): minimum, maximum and average on the walls.
5. **Decide whether the number can be trusted.**
   1. **Convergence.** Report `converged` and `solution_stable` as they are. A
      pressure drop that is neither converged nor stable is not a result.
   2. **Mesh sensitivity.** Repeat with `mesh.cell_size_m` about half, same
      inputs, and compare `pressure_drop_pa`. Report the change in percent and
      keep refining until it is within the tolerance the user agreed. Keep the
      cell count under the adapter's cap; if the cap stops you, say the result
      is not mesh-independent.
   3. **Flow balance.** `flow_balance_error` above about 1% means something is
      wrong with the case.
   4. **A hand calculation, when one exists.** For a straight round pipe at low
      Reynolds number: `32 * mu * L * v / D^2` (Hagen-Poiseuille), plus the
      developing-flow entrance term of about 1.25 to 1.28 times the dynamic
      pressure `0.5 * rho * v^2` when the pipe is not much longer than about
      `0.06 * Re * D`. For turbulent pipe flow use a friction-factor
      correlation, and say which. If there is no closed form for the geometry,
      say none was checked.
   5. **Turbulent runs are a screening estimate.** The k-omega SST path has not
      been validated against measurements. Report the `y_plus` range, and say
      that wall functions need the first cell in a suitable range.
6. **Record it** on a `core` connection with `twin.record_document`,
   `document_type: "simulation_result"`: the question, every input with its
   source, the surfaces and units, both mesh sizes and cell counts, the
   pressure drop, `converged`, `solution_stable`, the sensitivity result, the
   hand-calculation verdict, and the sentence "steady incompressible internal
   flow, OpenFOAM, no heat transfer modelled". Pass `analysed_geometry_node_id`
   only if the surfaces were derived from that exact geometry revision.
   Record modelling choices a reviewer could question (uniform inlet profile,
   fixed zero outlet pressure, laminar assumed) with `twin.record_decision`.
   Then go to step 8.

### 7. Convection, external flow or the rest: record the question, not an answer

1. Call `twin.record_document` with `document_type: "documentation"`, a
   `name` such as `Fan cooling, open analysis question`, and a `content` that
   states: the question, the part and revision, every input the user gave,
   every input still unknown, the acceptance limit, and which physics
   MetaForge does not solve.
2. If the user runs the analysis in their own tool and gives you the
   results, record them with `document_type: "simulation_result"`. The
   `content` must name the tool and version, the mesh, the turbulence model
   and boundary conditions **as the user reported them**, and say the run
   was performed outside MetaForge. Pass `analysed_geometry_node_id` only if
   the user confirms the run used that exact geometry revision.
3. Do not compute a velocity or heat transfer coefficient yourself and record
   it as a result. If the user asks for a hand estimate, show the formula and
   every input, label it an estimate, and record it, if at all, as
   `documentation`.
4. Record modelling choices a reviewer could question (ignoring radiation,
   treating a fin array as a block) with `twin.record_decision`, including
   the alternative.

### 8. Report

- Which physics was modelled and which was not.
- The answer with units against the limit, or "not established by
  MetaForge" for convection and external flow.
- Trust: convergence, mesh sensitivity, cross-check, sanity checks.
- What was recorded, with node ids; re-read
  `metaforge://twin/requirements/<project_id>` and give the requirement's
  status.

## Checks before you report

- [ ] The user agreed which physics the question needs
- [ ] Power, sink temperature and material came from the user or a cited source
- [ ] Faces chosen by coordinates, not by name
- [ ] Conductivity in W/(m*K); power is the total, in W
- [ ] Two mesh sizes compared by hand
- [ ] Every conduction result says "conduction only" or names the external tool
- [ ] Flow: fluid density and viscosity came from a cited source, not memory
- [ ] Flow: surfaces close, units stated, inside point is in the fluid
- [ ] Flow: `converged` and `solution_stable` reported as returned
- [ ] Flow: two mesh sizes compared; a turbulent result called a screening estimate
- [ ] No convection or external-flow number produced by you

## Failure handling

| Symptom | Likely cause | What to do |
|---|---|---|
| `-32001` naming `calculix` or `freecad` | Adapter container down | Tell the user; do not loop. |
| `-32001` naming `openfoam`, or "run the openfoam-adapter container" | The `cfd` profile is off on the host, or the container is down | Tell the user the flow solver is not running; do not loop. |
| "transient" refused | Only steady state is implemented | Say a transient answer is not available. |
| Peak temperature implausibly high | Power on a tiny face, or wrong sink face | Re-check the faces table and the wattage. |
| `min_temperature_c` not equal to the sink | Sink face wrong or not found | Re-pick the sink group by coordinates. |
| `stage_failed: snappyHexMesh`, "not inside the mesh" | `inside_point` is outside the fluid, or in the wrong units | Re-pick the point in the STL units. |
| `stage_failed: snappyHexMesh`, "no mesh faces on ..." or "leak" | The inlet, outlet and walls do not close | Fix the surfaces; check a gap at the joints. |
| `stage_failed: checkMesh`, over the cell cap | The mesh is too fine for the adapter | Use a larger `mesh.cell_size_m`, or say the case is too large. |
| `timed out` | The mesh or solve outran the adapter's time budget | Coarsen the mesh; do not retry unchanged. |
| `converged: false`, `solution_stable: false` | The run did not settle | Do not report the pressure drop; raise `max_iterations` or fix the case. |
| `flow_balance_error` above 1% | The case is inconsistent | Re-check surfaces, units and the inlet speed. |
| `twin.record_document` missing | Connected on `simulation`, not `core` | Record on a `core` connection; keep the outputs. |
| `record_document` held for approval | Writes need a person on this connection | Tell the user where it waits; do not retry. |
| User asks for fan cooling or external drag anyway | No solver exists | Record the question (step 7) and say so plainly. |

## Limits

- Conduction: no fluid flow, convection (film coefficient to air), radiation or
  transient heating. A conduction result is a bound at best for an air-cooled
  part. Heat is spread evenly over the source face; contact resistance at bolted
  joints is not modelled.
- Internal flow: steady, incompressible, constant properties, single phase, one
  fluid. The inlet speed is uniform and the outlet is fixed at zero gauge
  pressure. No heat transfer, no layer meshing, no rotating machinery, no
  compressibility. The pressure reported is static, area averaged on the inlet
  and outlet patches.
- Validation: a straight round pipe at Reynolds number 50, 0.4 m long. Background
  cell sizes of 8, 4 and 2 mm (15,096, 13,784 and 68,272 cells after refinement)
  gave pressure drops 12.8%, 3.4% and 0.1% below the analytic developing-flow
  value (Hagen-Poiseuille plus an entrance term of 1.25 times the dynamic
  pressure), so the result converges toward it as the mesh is refined. Input in
  millimetres and four processes reproduced the metre, single-process result
  exactly. The laminar path is checked against physics; the turbulent path is not.
- A result is valid only for the geometry revision it was run on; after the
  geometry changes it shows as `stale`.
