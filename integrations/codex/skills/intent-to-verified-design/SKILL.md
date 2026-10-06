---
name: intent-to-verified-design
description: Take a hardware product from a stated intent to a verified design in MetaForge, end to end. Use when the user describes something they want built ("design a wall shelf for 15 kg per shelf", "start a project for a 6-DOF arm"), asks to set up or plan a design flow, asks what the next step on a project is, or asks whether a design meets its requirements. Covers project setup, typed requirements, the gated design flow (propose, human approval, run), design work, verification evidence and the release gate. Do not use for a single isolated tool call such as one FEA run on an existing part; use the matching domain skill for that.
---

# Intent to verified design

This is the whole MetaForge lifecycle as one procedure. Each stage ends in a
state you can check, and most stages end at a point where a **person** has to
act. Your job is to do the work between those points accurately, record it in
the digital twin, and stop and say so when the next move is a human's.

Three rules hold for every stage:

1. **Never invent an engineering value.** A load, a material, a tolerance, a
   manufacturing route or a budget the user did not give is a question for the
   user, not a default for you to pick. Unknown is a valid answer and the
   server knows how to handle it ("loads unknown" keeps verification in the
   flow). A plausible guess is not.
2. **Never approve your own work.** Proposals, gates, waivers and promotions are
   decided by a person. There is no tool that approves a flow proposal and you
   must not look for a workaround. Report the approval id and stop.
3. **Report what the server said, not what you hoped.** `no_data` is a gap, not
   a pass. `unknown` is not `pending`. A held write is waiting, not failed. A
   short tool list may be a profile or a down adapter, not a smaller system.

## Stage 0: Check the connection

Call `health.check` once at the start.

- `status: degraded` lists `unreachable_adapters`. CAD needs `freecad` or
  `cadquery`, FEA needs `calculix`, electronics checks need `kicad`. If the
  stage you are about to reach needs one that is down, tell the user now
  rather than failing half way through.
- `profile.active` tells you which tool set this connection serves. The
  lifecycle crosses profiles:

  | Stage | Tools | Profile that serves them |
  |---|---|---|
  | Project, requirements, flow, decisions | `project.*`, `twin.record_constraint_set`, `flow.*`, `twin.record_decision`, `twin.record_document` | `core` |
  | Mechanical design | `freecad.*`, `cadquery.*`, `twin.commit_geometry` | `mechanical` |
  | Simulation and FEA | `calculix.*`, `twin.stage_work_product_file` | `simulation` |
  | Electronics checks | `kicad.*`, `component.*`, distributor tools | `electronics` |

  When a stage needs a tool your list does not have, check the profile before
  concluding anything is broken, and tell the user which profile to connect
  with. Do not describe the remaining tools as if they were everything.

## Stage 1: Project and session

1. If the user named a project, call `project.open` with their words as
   `query`. Several matches come back as an error listing them: ask which one,
   never pick.
2. If they described something new, still call `project.open` first. Only on
   `no project matches`, and only when the user asked you to set up a project,
   call `project.create` with a short `name` and their own words as
   `description`. A flow can be proposed without a project; do not create one
   just because it seems tidy, and never retry a `project.create` that was
   refused or timed out without asking.
3. Call `session.start` with the `project_id`, an `agent_code` naming your
   client and `task_type: "design_lifecycle"`. Read `project_scope_bound`. If
   it is `false`, pass `project_id` explicitly on every later call that takes
   one.
4. Read `metaforge://twin/brief/<project_id>` and
   `metaforge://twin/requirements/<project_id>`. Existing work changes every
   stage below: do not re-record requirements that are already there, revise
   them.

## Stage 2: Intent and requirements

Turn what the user said into requirements a gate can evaluate.

1. Separate **prose** (background, scope, what is out of scope) from **values**
   (mass, load, size, power, cost, safety factor, cycle count).
2. Prose goes to `twin.record_document` with `document_type: "prd"`. Values do
   not belong in the prd text: the server warns in `requirement_warning` when
   the prd states a value the constraint set lacks.
3. Values go to `twin.record_constraint_set`, one entry per requirement, each
   with:
   - `name`: short and stable, e.g. `shelf_load_per_shelf`
   - `metric` + `operator` + `limit` + `unit`, e.g. `deflection_mm`, `<=`, `3`,
     `mm`. A limit with no unit is not a requirement.
   - `verification_method`: `analysis`, `test`, `inspection` or `demonstration`
   - `expected_evidence`: the kind of artefact that would count (`calculation`,
     `simulation`, `test`, ...). `analysis` is the method; `simulation` is the
     evidence.
   - `acceptance_criteria`: what observation counts as meeting it, in the
     user's words
   - `severity`: `error` for a hard requirement, `warning` or `info` otherwise
   - `unit` must be one the server recognises (`mm`, `kg`, `N`, `MPa`, ...)
4. Read the reply. `incomplete` names every entry missing a binding, a unit, a
   verification method or acceptance criteria. Each one is recorded but cannot
   be checked, and it will look exactly like a requirement still waiting for
   evidence. Ask the user for the missing pieces and record a revision.
5. If `twin.record_engineering_entity` is in your tool list, also record the
   `intent` and each `stakeholder_need` as entities and link requirements to
   them with `parent_refs`, so the chain from what the user wanted to each
   number stays traceable. If the tool is not listed, say the trace link was
   not recorded rather than skipping it silently.

## Stage 3: Propose the design flow

A flow is a versioned template of gated phases. You tailor it; a person
approves it; only then can it run.

### 3a. Read the templates

Call `flow.list`. For every template it returns phase ids, each phase's
`required_deliverables`, `disciplines` and gate name. Use only those ids.
Current templates:

- `design_v1`: general lifecycle, intent through V&V
- `mech_v1`: a load-bearing part or mechanical subsystem
- `hardware_v1`: a multidisciplinary system with electronics and firmware

Pick by what the product contains, not by size. A shelf is `mech_v1`. A
product with a PCB or firmware is `hardware_v1`.

### 3b. Get the three required inputs from the user

`flow.propose` refuses to propose without these, and returns
`status: "needs_input"` with questions. Ask the user up front instead:

- **Manufacturing route** (`manufacturing_context.route`): `in_house`,
  `vendor` or `undecided`. With `in_house`, also `processes`, `machines`,
  `stock_materials` and `production_quantity`. `undecided` is valid: the
  server adds a route-selection decision phase.
- **Target maturity** (`target_maturity`): `concept`, `sim_validated`,
  `physically_validated` or `released`.
- **Loads and use** (`loads_and_use`): the loads with numbers, or the word
  `unknown`. Unknown keeps simulation in the flow; that is intended.

If `needs_input` comes back anyway, put its questions to the user verbatim.
Never answer them yourself.

### 3c. Write the tailoring yourself (caller mode)

Send `template` **and** `operations` together. That makes the server apply
your operations deterministically and check them strictly, instead of asking
its own model.

Operations, each with `op`, `phase` (an id from `flow.list`), `rationale`
and, where needed, `value`:

| `op` | `value` | Use when |
|---|---|---|
| `drop_phase` | none | The phase cannot apply to this product at all (no firmware in a plywood shelf). |
| `add_deliverable` | an artifact type the phase can produce | The product needs evidence the template does not demand, e.g. `test_plan` on a phase that verifies a physical load. |
| `set_disciplines` | list of disciplines | A phase must also involve another discipline. The server keeps the disciplines the phase's own deliverables need. |
| `set_model` | `provider:model` | Only when the user asked for a specific model on a phase. |
| `declare_items` | list of `{type, name}` | The product has several named deliverables of one type, e.g. two brackets. |

Rules for operations:

- Tailoring makes a flow stricter, never laxer. There is no operation to
  remove a gate or a deliverable, and inventing one is refused.
- Every rationale must follow from what the user stated: "plywood and a table
  saw, so CAD sized to sheet stock", not "simplifies the design".
- Only use artifact types that appear in some phase's deliverables or
  expected artifacts in `flow.list`. An invented type such as
  `shelf_deflection_report` is currently accepted by the server, and becomes a
  gate that no tool can ever satisfy.
- Do not drop simulation or V&V when loads are unknown. With known loads you
  may drop it only if you add a `test_plan` deliverable to a gated phase.
- `physically_validated` and `released` should add a `test_plan`.
- If the template is right as it is, send `operations: []` and tell the user
  you deliberately left it unchanged. **Never send `template` without
  `operations`:** the server treats that as "use this template unchanged" and
  does no tailoring at all, even though the tool description reads as if the
  server would tailor it.
- Pass `caller: {"client": "<your client>", "model": "<your actual model>"}`.
  It is recorded as provenance for the reviewer, so name the model you really
  are.

### 3d. Read the result

- A `422` / refusal names the operation, phase or invariant that failed. Fix
  that one thing and call again. Stop after two failed attempts and show the
  user the server's reason.
- `status: "proposed"` returns `approval_id`, `version_id`, `changes` and
  `phases`. Report all four, list each change with its rationale, and state
  that nothing runs until a person approves it in the MetaForge dashboard
  (or in your client, if it showed an inline approval prompt). Then **stop**.
  Do not poll for an approval you were not given.
- `changes: []` when you sent operations means every operation was a no-op
  (for example a deliverable that was already required). Say so.
- If you instead left the tailoring to the server (no `template`, no
  `operations`), check the result: a proposal on `design_v1` with no changes
  for a product that obviously needs a different template is a sign the
  server's generator did not run properly. Tell the user rather than
  presenting it as a tailored flow, and offer to resubmit it in caller mode.

## Stage 4: Run the approved flow

Only after the user tells you the proposal was approved:

1. `flow.start_run` with the `version_id` and a one-line `goal`. A refusal
   that says the version is not approved means nobody has answered yet; it is
   not an error to work around.
2. Follow the run with `flow.status <run_id>` or by reading
   `metaforge://flow/run/<run_id>`. Report each phase's state as given. A
   phase reading `unknown` means its state could not be read, so say unknown;
   never translate it into "not started".
3. Each phase ends at a gate a person answers. When a run is waiting on a
   gate, say which gate, what it checks, and that it is waiting for a person.

`run.start_design_flow` starts a run directly from a named template with no
tailoring and no proposal step. Use it only when the user explicitly asks to
run a stock template; the propose-then-approve path is the normal one.

## Stage 5: Design work inside the phases

Do each phase's work with the domain skills for that discipline (for example
`generate_cad`, `create_assembly`, `run_fea`, `run_erc`). Across all of them:

- **Name every part.** A CAD part's name becomes its STEP product name and its
  twin item name. Never `Part_1`, `Body`, `Box`.
- **Commit geometry by reference.** After `freecad.export_model`, call
  `twin.commit_geometry` with the same `session_id` and `obj_id` plus `name`.
  For stateless tools (`cadquery.create_parametric`,
  `cadquery.execute_script`, `freecad.create_parametric`), pass their returned
  `cad_file` as `file_path`. Do not paste base64 when a reference works.
- **Record decisions as you make them** with `twin.record_decision`: `title`,
  `rationale`, and `alternatives` you actually considered. A decision with no
  alternatives tells a reviewer nothing.
- **Revise, do not duplicate.** Writing to a name that already exists in the
  project revises that item. Pass `item_key` (and `change_reason`) when a name
  changed but it is the same thing.
- **A held write is expected.** A tool that writes may come back
  `approval_required` / held. Tell the user it is waiting for approval and
  where. Do not retry it, and do not reword the call to get round it.

## Stage 6: Verification evidence

A requirement is verified only by evidence pinned to the design revision it
was produced from.

1. For each requirement whose `verification_method` is `analysis`, pick the
   analysis that answers it (FEA for stress and deflection, thermal analysis
   for temperature, ERC/DRC for electrical rules, a power budget for power).
2. Persist the boundary conditions once: `twin.record_document` with
   `document_type: "load_case"` and `material`, `fixed_node_set`,
   `load_node_set`, `load_force_n`, `source_of_loads` as `metadata` keys.
   `source_of_loads` must name where the numbers came from (the user's
   statement, a standard, a measurement).
3. Run the analysis with the domain skill. For FEA that means staging the
   geometry with `twin.stage_work_product_file`, meshing, `calculix.run_fea`,
   then `calculix.check_mesh_convergence` across at least two element sizes,
   and a hand-calculation cross-check (`calculix.cross_check_cantilever_beam`
   and its siblings) where the geometry is a textbook case.
4. Record the result with `twin.record_document`, `document_type:
   "simulation_result"`, passing `analysed_geometry_node_id`,
   `analysed_geometry_revision`, `field_file` (the `field.file` the solver
   returned) and the `load_case_spec`. If `twin.record_evidence` is in your
   tool list, also record the evidence entity linking the result to the
   requirement. If it is not, tell the user the requirement link was not
   recorded on this connection.
5. Re-read `metaforge://twin/requirements/<project_id>`. Report each
   requirement's live status: `pass`, `fail`, `uncertain`, `stale` (the
   evidence is for an older revision) or `no_data` (no evidence at all).
   `twin.what_verifies <requirement node>` lists what supports one
   requirement.

Never report a number you did not get from a tool. If convergence failed, or
a cross-check disagreed, the honest result is "not yet established", and the
next step is the fix, not the claim.

## Stage 7: Gate review and release

1. Read the requirements matrix and list every requirement that is not
   `pass`. `uncertain`, `stale` and `no_data` all block a gate. A `fail` is
   overridden only by a waiver a person approved for that requirement.
2. `twin.constraint_violations` with the `project_id` gives what is currently
   broken across the project; report it alongside the matrix.
3. If `twin.attempt_promotion` is in your tool list, it is the real gate: it
   refuses when a required claim is unsatisfied and is always held for a
   person. Do not pass `decided_by`; whoever approves is recorded as the
   deciding authority. If the tool is not listed, the flow's own gates are
   the release decision; say so.
4. Waivers and release approvals are `twin.record_engineering_entity`
   entities (`waiver`, `release_approval`). Recording one leaves it
   `proposed`, which fails the release gate. Only call
   `twin.approve_engineering_entity` when the user, as the responsible
   person, explicitly tells you to approve that specific entity, and name
   them as `approved_by`.

## Failure handling

| What you see | What it means | What to do |
|---|---|---|
| `-32001` with an adapter name | That tool's container is down | Tell the user which adapter; do not retry in a loop. |
| `approval_required` / held | A person must approve this write | Report where it is waiting; do not retry or reword. |
| `timed_out` on an approval | Nobody answered in the window | Ask the user before trying again. |
| `needs_input` from `flow.propose` | Required inputs missing | Ask the user the listed questions verbatim. |
| `422` from `flow.propose` | An operation, phase or invariant failed | Fix the named problem; at most two retries. |
| `503` from `flow.propose` | The server's generator could not reach a model | Offer caller mode (send `template` + `operations`). |
| `incomplete` from `twin.record_constraint_set` | Requirements recorded but not checkable | Ask for the missing fields; record a revision. |
| A tool this skill names is not in your list | Wrong profile, or the server did not wire it | Check `health.check` `profile`; tell the user which. |
| Phase state `unknown` | The run's state could not be read | Say unknown; check `metaforge://health/connection`. |

## What to tell the user at the end of a turn

- What you recorded, with the ids the server returned.
- What is waiting for a person, with approval ids and where to answer them.
- Every requirement that is not `pass`, and what evidence would close it.
- Anything this connection could not do (profile, missing tool, down adapter).
