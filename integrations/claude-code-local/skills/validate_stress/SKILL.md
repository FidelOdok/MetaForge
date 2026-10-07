---
name: validate_stress
description: Give a pass or fail verdict on whether a part's peak von Mises stress stays within an allowable stress (limit stress divided by a required safety factor) under a stated static load, using a CalculiX static solve, then record the verdict pinned to the geometry revision. Use when the user asks "does it pass", "is the safety factor at least 2", "is the bracket within allowable stress at 200 N", or when a requirement states a stress limit or minimum safety factor. For deflection, natural frequency or an open-ended stress study use run_fea instead.
domain: mechanical
---

# validate_stress

Answer one yes-or-no question: under this static load case, does the part's
peak von Mises stress stay at or below the allowable stress the user set? It
runs a static CalculiX solve and compares the trusted peak stress with
`limit stress / required safety factor`. The verdict is only as good as the
number behind it, so convergence and the accuracy flag decide whether you may
say "pass" at all.

## When to use it

- "Does the bracket stay under yield with a safety factor of 2 at 200 N?"
- "Check the arm link against its 120 MPa allowable."
- A requirement such as `safety_factor >= 2` or `max_von_mises_mpa <= 80`
  with `verification_method: analysis`.

Use `run_fea` instead for:

- Deflection or displacement limits, natural frequencies (modal), or "what
  is the stress" with no pass/fail limit to compare against.
- Building the full evidence trail from scratch: `run_fea` owns the load case
  record and the simulation result record. This skill reuses its steps and
  adds the verdict.

Not for: temperature limits (`calculix.run_thermal`), fatigue, buckling,
contact or plasticity (not modelled here; tell the user).

## Tools and profile

| Step | Tool | Profile that serves it |
|---|---|---|
| Find the part | `twin.find_by_property`, `twin.get_node` | every profile |
| Stage the geometry | `twin.stage_work_product_file` | `simulation` |
| Mesh | `freecad.generate_mesh` | `simulation` |
| Check the mesh | `calculix.validate_mesh` | `simulation` |
| Solve | `calculix.run_fea` | `simulation` |
| Accuracy flag | `calculix.extract_results` | `simulation` |
| Convergence | `calculix.check_mesh_convergence` | `simulation` |
| Hand calculation | `calculix.cross_check_cantilever_beam` | `simulation` |
| Record result and load case | `twin.record_document` | `simulation`, `core`, full set |
| Record a modelling choice | `twin.record_decision` | every profile |

The analysis and the recording both run on the **`simulation`** profile.
If `twin.record_document` is missing from your list (an older server served
it on `core` only), finish the analysis, report it, and tell the user the
result was not recorded and that a connection with `?profile=core` can.

## Inputs you need before you start

Ask for anything missing. Do not default a material, a load or a safety
factor, even where the skill or tool has a default.

| Input | Why it matters | Example |
|---|---|---|
| The part | Node id or name; the verdict is for that revision only | `Upper Arm Link` |
| Material | Elastic properties for the solve | `aluminum_6061`, `steel`, or explicit E and Poisson's ratio in MPa |
| Limit stress, MPa, and its source | The stress the safety factor is applied to, usually yield | 276 MPa, 6061-T6 yield from the user's datasheet |
| Required safety factor | Allowable = limit / safety factor | 2.0 |
| Fixed face, in words | Mapped to a mesh surface group by coordinates | "bolt face at x = 0" |
| Load face, total force vector in N, and its source | A load with no source is not evidence | `[0, 0, -200]` on the tip face, from the user's spec |

Ask whether the stress limit they gave is already derated. If the user says
"allowable is 120 MPa" and also "safety factor 2", confirm whether 120 is the
yield or the allowable; applying the factor twice halves the margin.

If the project already has a `load_case` document for this part, reuse it.

## Procedure

### 1. Geometry and mesh

Follow `generate_mesh`:

1. `twin.stage_work_product_file` with the part's `node_id`; note the
   revision.
2. `freecad.generate_mesh` with `input_file` = the staged path,
   `output_format: "inp"`, an `element_size` derived from the thinnest
   load-carrying section, `element_order: 2` for bending.
3. From the `faces` table pick the fixed and load surface groups by
   `bbox_mm`, `centroid_mm` and `normal`. Never by the `Surface<n>` name.
4. `calculix.validate_mesh` on `mesh_file`; re-mesh if `valid` is false.

### 2. Solve

Call `calculix.run_fea` with:

- `mesh_file`, `load_case` (a label such as `bracket_200N_static`)
- `analysis_type: "static_stress"`
- `material`: `{"name": "aluminum_6061"}` or
  `{"youngs_modulus_mpa": 68900, "poissons_ratio": 0.33}` (MPa, not Pa)
- `fixed_node_set`, `load_node_set`: the groups from step 1
- `load_force_n`: `[Fx, Fy, Fz]` total newtons, in the twin frame

Read `max_von_mises` (a single `global` value; there is no per-region
breakdown), `displacement`, `frd_path`, `field`, and `warnings`. A warning
about `fixed_node_set` spanning most of the part means the wrong face was
chosen: fix it before going on.

### 3. Decide whether the peak stress can be trusted

1. `calculix.extract_results` with `frd_path`. Read the stress block's
   `accuracy` field. A flagged peak is usually a point-load or fixed-edge
   singularity: the "max" is a mesh artefact that grows as you refine. Spread
   the load over a real face and report the stress away from the
   singularity, saying so.
2. Re-mesh at about half the element size with identical boundary
   conditions, solve again, and call `calculix.check_mesh_convergence` with
   `points` = `[{"element_size_mm": h1, "max_von_mises_mpa": s1},
   {"element_size_mm": h2, "max_von_mises_mpa": s2}]`. Not converged means
   refine again or raise the element order.
3. For a rectangular cantilever with a tip load, call
   `calculix.cross_check_cantilever_beam` with `length_mm`, `width_mm`,
   `height_mm`, `force_n` and `fea_max_stress_mpa`. Disagreement usually
   means a wrong face. For other shapes, say no closed-form check applies.

### 4. Compute the verdict yourself

With the converged peak stress `s`:

- allowable = limit stress / required safety factor
- achieved safety factor = limit stress / `s`
- **pass** when `s` <= allowable (equivalently, achieved factor >= required)
- margin = allowable - `s`, in MPa and as a percentage of allowable

If convergence failed or the accuracy flag is unresolved, the verdict is
**not established**, not pass and not fail. Say what would settle it.

### 5. Record

If `twin.record_document` is in your list:

1. Once per part, `document_type: "load_case"` with `metadata` keys
   `material`, `fixed_node_set`, `load_node_set`, `load_force_n`,
   `source_of_loads`.
2. `document_type: "simulation_result"` with `name` (part, load, revision),
   `content` (load case, material, mesh sizes and orders, peak stress,
   limit stress and its source, required and achieved safety factor,
   verdict, convergence and cross-check results), `metadata` with
   `max_von_mises_mpa`, `allowable_mpa`, `safety_factor_required`,
   `safety_factor_achieved` and `passed` as top-level keys,
   `analysed_geometry_node_id`, `analysed_geometry_revision`, `field_file` =
   the `file` value inside the `field` object the solve returned, and `load_case_spec`.

If `twin.record_evidence` is in your list, also link the result to the
requirement it verifies; if not, say the requirement link was not recorded.
Record modelling choices a reviewer could dispute (bolts modelled as a fixed
face) with `twin.record_decision`, with the alternative.

### 6. Report

The verdict, the peak stress with units, the allowable and how it was
derived, achieved vs required safety factor, convergence and cross-check
status, the node ids recorded, and the requirement's status from
`metaforge://twin/requirements/<project_id>`.

## Checks before you say "pass"

- [ ] Material, load, limit stress and safety factor came from the user or a cited source
- [ ] Safety factor applied once, to a limit stress that was not already derated
- [ ] Fixed and load faces identified by coordinates
- [ ] Mesh validated; peak stress converged across two or more sizes
- [ ] Accuracy flag read; any singularity explained
- [ ] Verdict pinned to the exact geometry revision, or its absence stated

## Failure handling

| Symptom | Likely cause | What to do |
|---|---|---|
| `-32001` naming `calculix` or `freecad` | Adapter container down | Tell the user; do not loop. |
| "unknown material" | Name not in the solver's table | Ask for the right name or explicit E and Poisson's ratio in MPa. |
| Stress orders of magnitude off | Pa instead of MPa, or kN vs N | Fix units; re-solve. |
| Peak grows with every refinement | Singularity at a point load or sharp corner | Load a face; report stress away from it, or "not established". |
| Zero displacement or rigid motion | Wrong or missing `fixed_node_set` | Re-check the faces table. |
| A `warnings` entry on the solve | Suspect boundary condition | Fix it before trusting any number. |
| `twin.record_document` not in your list | Connected on `simulation` | Report the verdict; tell the user it was not recorded and how to record it. |
| Write `approval_required` / held | Writes need a person | Report where it waits; do not retry. |
| A `simulation` tool not in your list | Wrong profile | Check `health.check` `profile`; ask for `?profile=simulation`. |

## Limits

- Linear static, isotropic material, small deflection. No modal, thermal,
  fatigue, buckling, contact or plasticity.
- One global peak von Mises stress per solve; no named regions.
- Loads spread evenly over the chosen face; bolt preload is not modelled.
- A verdict is valid for the revision analysed. A new revision makes it stale.
