---
description: Refine one component's CAD automatically in MetaForge's self-validating design loop. A model writes CadQuery from the component's parametric plan, it is built and checked (validity, measurements, DFM for the selected process), rendered in a headless browser and reviewed by a vision critic, and revised until the critic is satisfied, every check passes, or a cap is reached. Use when the user asks to improve, refine, fix or iterate on a part's CAD model, to fix a DFM finding on a part, or to make a part match its brief, and the part has a parametric plan.
---

# design_cad_iterative

Let MetaForge iterate on a part's CAD for you, and hand the person a proposed
version to accept. You start the loop; MetaForge does the writing, building,
rendering and reviewing in its own adapters. You never run CadQuery yourself
for this, and you never accept the result for the person.

## When to use it

- "Refine the base plate model", "make the housing match the brief".
- "The DFM check says the wall is too thin; fix it."
- A review of rendered views shows something wrong that a few edits can fix.

Not for:

- A part with no parametric plan yet. Make it editable first (Create's
  *Make it editable*, or the reconstruction flow), then come back.
- Writing a one-off script the user dictated: that is `generate_cad_script`.
- Accepting a proposal. Only the person does that, in Create (*Use this
  version*) or Engineering.

## Steps

1. Find the part's parametric plan: its item key (`PSK-...`) or its
   component id (`CMP-...`). Ask the user which part if it is unclear.
2. Call `twin.design_cad_loop` with `project_id`, `sketch_key` (or
   `component_id`), and `intent` in the user's words. Add
   `target_dimensions` (`x`, `y`, `z` in mm) only when the user stated the
   overall size, and `tolerance_mm` if they gave one. Leave
   `max_iterations` at 5 unless asked.
3. It returns a job. Poll `twin.job_status` with the job id until it ends;
   each iteration also shows on the activity overlay as "Iteration 2: fixing
   wall thickness".
4. Read the result's `stop_reason` and say it plainly:
   - `satisfied`: the critic is satisfied, validity passed, no blocking DFM
     finding remains and stated sizes are within tolerance.
   - `iteration_cap` or `spend_cap`: stopped with issues still open; list
     `result.open_issues`.
   - `needs_input`: a dimension is unknown. Put each of `questions` to the
     user, then run again with their numbers in `answers` (by the question's
     `name`). Never answer one yourself.
   - `failed`: a model or adapter could not be reached; say which from
     `stop_detail`.
5. Tell the user a proposed version is waiting for them, with its working
   values (`result.assumptions`) for them to confirm. The proposal is not
   applied until they choose *Use this version*.

## Rules

- Never invent a dimension. If the user does not want working values, pass
  `allow_working_values: false` and the loop asks instead.
- The critic's verdict is a review, not a check: never call a proposed
  version validated or verified. Only the exact CAD build after acceptance
  validates geometry.
- Each run makes paid model calls. Do not start a second run on the same
  part while one is running, and respect a held call: the person decides.
