---
description: Design-for-manufacture (DFM) review of a finished design against the process chosen at the route gate. Check wall thickness, holes, inside corner and bend radii, draft, overhang and envelope against the process's sourced limits, report pass, fail or unknown per feature with the margin and the limit's source, write the manufacturing plan, and record the verdict for the DFM gate. Use in a flow's DFM Review phase, or when the user asks whether a design can be made by a process, wants a DFM review, or asks how a designed part will be made.
---

# check_manufacturability

The process was chosen before the design (see
`rank_manufacturing_processes`). Now the design exists, check it against
that process, feature by feature, and write down how it will be made. A
failure here sends the design back for rework before simulation is spent on
it.

## When to use it

- The **DFM Review** phase of a design flow (after design, before simulation
  in `mech_v1`).
- "Can this be laser cut and welded as drawn?"
- "Do a DFM pass on the bracket before we order it."

Not for:

- Choosing the process: `rank_manufacturing_processes`.
- Whether tolerances on a drawing are holdable: `check_tolerance`.
- Strength: `run_fea`.

## What you can and cannot measure

MetaForge cannot yet measure a wall thickness, a hole diameter, a radius or a
draft angle off the geometry. Say so; do not pretend otherwise. Use:

| Value | Where it comes from | basis |
|---|---|---|
| envelope, volume, mass | `twin.get_node` on the cad_model (`bbox_mm`), `freecad.measure` | `measured` |
| overhang for 3D printing | `twin.evaluate_overhang_metric` | `measured` |
| wall, hole, radius, draft, bend values the part was built with | `freecad.describe_session` (authoring parameters), the design decision | `declared` |
| anything else | the person, or the design phase's records | `stated` |

Every limit comes from the process chosen at the route gate: the constraint
set and the `design_value` entries recorded there, each with its source. A
feature with no sourced limit is `unknown`, never a pass.

## Steps

1. Read the route decision: the chosen process and its constraint set. When
   the part is made on a machine from the machine library (the flow context
   lists them), call `library.machine_limits` with its id and use its
   `limits` as they are: each already cites its source, and a limit the
   machine has no figure for is absent, so that check reads unknown. Name its
   `needs_review` figures in the summary.
2. Collect the features and their values, each with its basis (table above).
3. Run the check: every feature against every limit of its kind.
4. Record each failing or unknown finding with `twin.record_evidence` against
   the cad_model, and a `twin.record_claim` per DFM requirement you can state
   ("leg wall at least 1.5 mm for laser cut + weld"), so the completion verdict
   sees them.
5. Write the manufacturing plan: stock to buy (sections, sheet sizes,
   quantities), the operations in order, the quantity, and a unit cost
   estimate only if every input is sourced.
6. Record the decision with `twin.record_decision` and `dfm_review`:
   `{process, verdict, findings: {pass, fail, unknown}, unknowns, plan:
   {stock, operations, quantity, unit_cost_estimate, cost_source}}`.

## The DFM gate

- No `dfm_review` recorded: not ready.
- `not_manufacturable`: not ready. Rework the design (the gate offers rework
  back to the design phase) or reopen the process choice.
- `incomplete`: ready, and the gate summary lists what could not be checked;
  approving accepts it.
- `manufacturable`: ready, with the process and plan named.

## Limits

- No geometry measurement of walls, holes, radii, bends or draft yet; those
  values are declared or stated, and the findings say which.
- No GD&T, surface finish, tool-access or assembly-sequence checks.
- Cost is only as good as its sourced inputs.

## Checklist

- [ ] process and limits taken from the route decision, not re-chosen
- [ ] every feature carries its basis; unmeasurable ones are not called measured
- [ ] failing and unknown findings recorded as evidence
- [ ] manufacturing plan with stock and operations
- [ ] `dfm_review` recorded on the decision
