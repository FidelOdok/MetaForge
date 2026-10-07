---
description: Handle a fluid flow or cooling question about a committed part honestly. MetaForge has no CFD solver, so this skill runs the steady-state conduction model it does have when that answers the question, and otherwise records the question, the assumptions and any results the user brings from their own CFD tool. Use when the user asks for CFD, airflow, pressure drop, flow velocity, fan or duct sizing, convection cooling, or "will this part stay below N degrees", or when a requirement needs flow or thermal evidence.
---

# run_cfd

Answer a flow or cooling question about a real part without overstating what
MetaForge can compute. The server has **no fluid solver**: nothing on it
computes velocity, pressure or convective heat transfer. What it does have is
a steady-state **conduction** solve (`calculix.run_thermal`): a heat source on
one face conducting through the part to a face held at a fixed temperature.
Decide with the user which of those their question actually is, run the
conduction model only when it fits, and record everything else as an open
question rather than a number.

## When to use it

- "What pressure drop does this duct give at 2 m/s?" (flow: no solver here)
- "Is a 40 mm fan enough to cool the enclosure?" (convection: no solver here)
- "The regulator dissipates 1.5 W into the bracket, which is bolted to a
  chassis at 40 C. How hot does the regulator pad get?" (conduction: can be
  run)
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
| Re-read results | `calculix.extract_results` | `simulation` |
| Hand-calc cross-check | `calculix.cross_check_thermal_steady_state` | no-profile connection only |
| Record result or question | `twin.record_document` | `core` |
| Record modelling choices | `twin.record_decision` | every profile |

The solve runs on **`simulation`** and the result document is written on
**`core`**. If one connection does not serve both, solve on `simulation`,
keep the outputs, and record on a `core` connection; tell the user which
profile to use for each. If a tool is missing, check `health.check` ->
`profile` before assuming anything is broken.

There is no CFD or flow tool of any kind on the server. Do not look for a workaround,
and do not present `calculix.run_thermal` output as a flow result.

## Inputs you need before you start

Ask the user for anything missing. Do not fill these in with defaults.

| Input | Why it matters | Example |
|---|---|---|
| The question in physical terms | Decides flow, convection or conduction | "regulator pad temperature" vs "pressure drop at 2 m/s" |
| The part | Its twin node id, or its name in the project | `PSU Bracket` |
| Material | Thermal conductivity drives the answer | `aluminum_6061`, or `{"thermal_conductivity_w_mk": 167}` |
| Heat source face and power | Where the heat enters, total watts | "the regulator pad, 1.5 W" |
| Where the power figure came from | A wattage with no source is not evidence | the component's datasheet, a measurement |
| Sink face and its temperature | Where the heat leaves, held at a fixed temperature | "the two mounting tabs, 40 C" |
| Acceptance limit | What counts as a pass | "pad <= 85 C" |

For a flow or convection question, ask for: the fluid, inlet conditions
(velocity or flow rate, temperature), outlet conditions, the heat loads, and
the limit. You will record these, not solve them.

## Procedure

### 1. Classify the question with the user

State back which physics the question needs:

- **Conduction to a fixed-temperature sink**, no air movement modelled:
  go to step 2.
- **Flow** (velocity, pressure drop, flow split) or **convection** (cooling
  by air or liquid, fan sizing, natural convection to ambient): go to step 6.
  Say plainly that MetaForge cannot solve this.

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
- `material`: `{"name": "aluminum_6061"}` or
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
to step 7.

### 6. Flow or convection: record the question, not an answer

1. Call `twin.record_document` with `document_type: "documentation"`, a
   `name` such as `Duct pressure drop, open analysis question`, and a
   `content` that states: the question, the part and revision, every input
   the user gave, every input still unknown, the acceptance limit, and that
   MetaForge has no CFD solver.
2. If the user runs the analysis in their own tool and gives you the
   results, record them with `document_type: "simulation_result"`. The
   `content` must name the tool and version, the mesh, the turbulence model
   and boundary conditions **as the user reported them**, and say the run
   was performed outside MetaForge. Pass `analysed_geometry_node_id` only if
   the user confirms the run used that exact geometry revision.
3. Do not compute a pressure drop, velocity or heat transfer coefficient
   yourself and record it as a result. If the user asks for a hand estimate,
   show the formula and every input, label it an estimate, and record it, if
   at all, as `documentation`.
4. Record modelling choices a reviewer could question (ignoring radiation,
   treating a fin array as a block) with `twin.record_decision`, including
   the alternative.

### 7. Report

- Which physics was modelled and which was not.
- The answer with units against the limit, or "not established by
  MetaForge" for flow and convection.
- Trust: mesh sensitivity, cross-check, sanity checks.
- What was recorded, with node ids; re-read
  `metaforge://twin/requirements/<project_id>` and give the requirement's
  status.

## Checks before you report

- [ ] The user agreed which physics the question needs
- [ ] Power, sink temperature and material came from the user or a cited source
- [ ] Faces chosen by coordinates, not by name
- [ ] Conductivity in W/(m*K); power is the total, in W
- [ ] Two mesh sizes compared by hand
- [ ] Every result says "conduction only" or names the external tool
- [ ] No flow or convection number produced by you

## Failure handling

| Symptom | Likely cause | What to do |
|---|---|---|
| `-32001` naming `calculix` or `freecad` | Adapter container down | Tell the user; do not loop. |
| "transient" refused | Only steady state is implemented | Say a transient answer is not available. |
| Peak temperature implausibly high | Power on a tiny face, or wrong sink face | Re-check the faces table and the wattage. |
| `min_temperature_c` not equal to the sink | Sink face wrong or not found | Re-pick the sink group by coordinates. |
| `twin.record_document` missing | Connected on `simulation`, not `core` | Record on a `core` connection; keep the outputs. |
| `record_document` held for approval | Writes need a person on this connection | Tell the user where it waits; do not retry. |
| User asks for airflow or pressure anyway | No solver exists | Record the question (step 6) and say so plainly. |

## Limits

- No fluid flow, convection (film coefficient to air), radiation or
  transient heating. A conduction result is a bound at best for an
  air-cooled part.
- Heat is spread evenly over the source face; contact resistance at bolted
  joints is not modelled.
- A result is valid only for the geometry revision it was run on; after the
  geometry changes it shows as `stale`.
