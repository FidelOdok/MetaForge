---
name: run_fea
description: Run a static stress or modal finite element analysis on a committed CAD part with CalculiX, check that the answer can be trusted, and record it in the twin pinned to the geometry revision it analysed. Use when the user asks for stress, deflection, safety factor, natural frequency or "will this hold", or when a requirement with verification_method analysis needs structural evidence.
domain: simulation
---

# run_fea

Answer a structural question about a real part with a finite element
analysis, and leave behind evidence a reviewer can trust: the load case it
used, the mesh it ran on, proof the mesh was fine enough, and a result pinned
to the exact design revision.

## When to use it

- "What is the max stress / deflection on the bracket at 150 N?"
- "Does the shelf meet its 3 mm deflection requirement?"
- "What is the first natural frequency of the arm link?"
- A requirement's `verification_method` is `analysis` and its metric is
  stress, displacement, safety factor or frequency.

Not for: temperature (use `calculix.run_thermal`), joint reactions on a
mechanism (`calculix.compute_joint_loads`), or a part with no committed
geometry yet (design it first with `generate_cad`).

## Tools and profile

Connect with the **`simulation`** profile (`?profile=simulation`). It serves
every tool below except `calculix.cross_check_cantilever_frequency`, which is
only served on a connection with no profile; skip that cross-check, or say it
needs a full connection. If a tool is missing, check `health.check` →
`profile` before assuming anything is broken.

| Step | Tool |
|---|---|
| Get the CAD file | `twin.stage_work_product_file` |
| Mesh it | `freecad.generate_mesh` |
| Check the mesh | `calculix.validate_mesh` |
| Solve | `calculix.run_fea` |
| Re-read results | `calculix.extract_results` |
| Convergence | `calculix.check_mesh_convergence` |
| Hand-calc cross-check | `calculix.cross_check_cantilever_beam`, `calculix.cross_check_cantilever_frequency` |
| Record | `twin.record_document`, `twin.record_decision` |

## Inputs you need before you start

Ask the user for anything missing. Do not fill these in with defaults.

| Input | Why it matters | Example |
|---|---|---|
| The part | Its twin node id, or its name in the project | `Upper Arm Link` |
| Material | Elastic properties, and density for modal | `aluminum_6061`, `steel` |
| Where it is held | Which face is fixed | "the two bolt holes at x = 0" |
| Where the load acts and how much | Face plus a total force vector in N | "150 N downward on the tip face" |
| Where the load number came from | A load with no source is not evidence | the user's spec, a standard, a measurement |
| The acceptance limit | What result counts as a pass | "max deflection <= 3 mm" |

If the project already has a `load_case` document for this part, reuse it
rather than asking again.

## Procedure

### 1. Find the geometry and stage it

1. Find the part's CAD work product: read `metaforge://twin/brief/<project_id>`
   or call `twin.find_by_property` / `twin.get_node`. Note its node id **and
   its revision**; the result will be pinned to both.
2. Call `twin.stage_work_product_file` with that `node_id`. It returns a
   local `file_path` the CAD and FEA tools can read. Use it even if you
   authored the part yourself earlier: a FreeCAD session id goes stale, the
   staged file does not.

### 2. Mesh it and identify the faces

1. Call `freecad.generate_mesh` with `input_file` = the staged path and
   `output_format: "inp"`. Start with an `element_size` around a tenth of the
   part's smallest important dimension.
2. Read the `faces` table in the result. gmsh names surfaces `Surface1`,
   `Surface2`, ...; the names say nothing about which face is which. Pick the
   fixed face and the load face by their **bounding box, centroid and
   normal** (for example "the face whose bbox has x_max = 0 is the wall
   mount"). State which group you chose and why.
3. Call `calculix.validate_mesh` on the `mesh_file`. A failed quality check
   means fix the mesh (smaller element size, different algorithm) before
   solving.

### 3. Record the load case once

Call `twin.record_document` with `document_type: "load_case"`, a clear
`name` (`shelf_15kg_static`), and these keys in `metadata`: `material`,
`fixed_node_set`, `load_node_set`, `load_force_n`, `source_of_loads`. Later
runs on new revisions reuse it instead of retyping the boundary conditions.

### 4. Solve

Call `calculix.run_fea`:

- `mesh_file`: from step 2
- `load_case`: the load case name (a label only)
- `analysis_type`: `static_stress` or `modal`
- `material`: `{"name": "aluminum_6061"}`, or explicit
  `{"youngs_modulus_mpa": 69000, "poissons_ratio": 0.33}`. **MPa, not Pa**:
  mesh coordinates are millimetres, and Pa understates stiffness by 10^6.
  `modal` needs `name` even with explicit properties, because only the
  named lookup has a density.
- `fixed_node_set`: the face group from step 2
- `static_stress` only: `load_node_set` and `load_force_n` as `[Fx, Fy, Fz]`
  **total** force in newtons in the twin's frame (15 kg on a shelf is
  `[0, 0, -147.1]`)
- `modal` only: `num_modes` (default 3)

Keep the returned `max_von_mises`, displacement, `frequencies_hz`, the
`.frd` path and `field.file`.

### 5. Decide whether the number can be trusted

Do all three before reporting a result as an answer:

1. **Accuracy flag.** `calculix.extract_results` on the `.frd` returns a
   stress block with an `accuracy` field. A flagged result usually means a
   point-load or boundary-condition stress concentration, not real stress.
   Spread the load over a face, or report stress away from the singularity.
2. **Convergence.** Re-mesh at a second, finer `element_size` (about half),
   solve again with identical boundary conditions, then call
   `calculix.check_mesh_convergence` with `points` like
   `[{"element_size_mm": 4, "max_von_mises_mpa": 41.2}, {"element_size_mm": 2,
   "max_von_mises_mpa": 43.0}]`. Not converged means refine again; do not report the coarse number.
3. **Hand calculation**, when the part is the textbook case (rectangular
   cantilever, fixed at one end, tip load): `calculix.cross_check_cantilever_beam`
   for stress, `calculix.cross_check_cantilever_frequency` for the first mode.
   A disagreement beyond tolerance usually means the wrong face was fixed.
   For other geometry, say no closed-form check applies.

### 6. Record the result

Call `twin.record_document` with:

- `document_type: "simulation_result"`
- `name`: part, load case and revision, e.g. `Shelf deflection, 15 kg, rev 3`
- `content`: a short summary: load case, material, mesh sizes, max stress,
  max displacement, safety factor against yield, convergence verdict, cross-
  check verdict, and anything you could not establish
- `analysed_geometry_node_id` and `analysed_geometry_revision`: from step 1
- `field_file`: the `field.file` the solver returned, so the dashboard shows
  a 3D contour on the right geometry
- `load_case_spec`: the boundary conditions you used

If `twin.record_evidence` is in your tool list, also record evidence linking
this result to the requirement it verifies. If not, say that the requirement
link was not recorded on this connection.

When you made a modelling choice a reviewer could disagree with (fixing a
face rather than modelling bolts, ignoring a fillet), record it with
`twin.record_decision` including the alternative.

### 7. Report

- The answer with units, and the limit it is compared against
- Margin: safety factor against yield, or headroom against the requirement
- How trustworthy it is: converged or not, cross-check result, accuracy flag
- What was recorded, with node ids
- Re-read `metaforge://twin/requirements/<project_id>` and give the
  requirement's new status

## Checks before you say "pass"

- [ ] Load magnitude and direction came from the user or a cited source
- [ ] The fixed and loaded faces were identified by coordinates, not by name
- [ ] Units: forces in N, moduli in MPa, lengths in mm
- [ ] Mesh validated, and converged across at least two sizes
- [ ] Accuracy flag read; any concentration explained
- [ ] Result recorded against the exact geometry revision

## Failure handling

| Symptom | Likely cause | What to do |
|---|---|---|
| `-32001` naming `calculix` or `freecad` | Adapter container down | Tell the user; do not loop. |
| "mesh_file not found" | Passed a work product id or a stale path | Stage again with `twin.stage_work_product_file`. |
| Stress orders of magnitude off | Pa instead of MPa, or N vs kN | Fix units and re-solve. |
| Huge stress at one node only | Point load or fixed-edge singularity | Load a face; report stress away from it. |
| Displacement zero or rigid motion | Wrong or missing `fixed_node_set` | Re-check the faces table. |
| Not converged after three sizes | Singularity or too-coarse geometry feature | Report "not established" with the trend. |
| `record_document` held for approval | Writes need a person on this connection | Tell the user where it waits; do not retry. |

## Limits

- Linear static stress and modal only. No contact, plasticity, large
  deflection or fatigue: say so when the question needs them.
- Loads are spread evenly over the chosen face. Bolt preload and bearing
  contact are not modelled.
- A result is valid only for the revision it was run on. After the geometry
  changes, the old result shows as `stale` and must be re-run.
