---
description: Check an electronics design's power budget by summing worst-case load on each supply rail, comparing it against what the rail can deliver with the user's margin, flagging any rail over budget, and recording the sourced budget in the twin. Use when the user asks whether a regulator or battery can supply the board, asks for a power or current budget per rail, asks how much headroom a rail has, or when a power or current requirement needs analysis evidence.
---

# check_power_budget

Answer "can each supply rail carry its load, with margin?" for a real design,
and leave behind a budget a reviewer can audit: every load with the source of
its number, every rail's capacity with its source, the margin rule, and a
per-rail pass or fail.

**There is no MetaForge tool that computes a power budget.** The skill's
server-side handler is not implemented, and no MCP tool exposes it. You do
the arithmetic yourself, from figures you read out of the design and its
datasheets with real tools, and you record the result with real twin tools.
Say this to the user so nobody mistakes your sum for a tool's output.

## When to use it

- "Can the 3V3 LDO handle everything on that rail?"
- "What is the current budget per rail on the flight controller?"
- "How much headroom does the 5 V buck have with the radio transmitting?"
- A requirement such as `power_draw_mw <= 500` has `verification_method`
  `analysis`.

Not for: battery runtime over a mission profile (needs duty cycles over time;
do it only if the user gives the profile, and call it an estimate), thermal
rise in a regulator (`calculix.run_thermal` with a real dissipation figure),
transient or inrush behaviour (no tool here simulates circuits), or choosing
a new regulator (`component.search_parametric`, then a budget re-check).

## Tools and profile

No single profile serves every step. `kicad.*` and `component.*` are on
**`electronics`**; `web.*`, `knowledge.ingest` and `twin.record_document`
are on **`core`**. A connection with no `?profile=` serves all of them. If a
tool below is missing, check the `profile` field of `health.check` and tell the
user, rather than working around it silently.

| Step | Tool | Profile |
|---|---|---|
| Read requirements and existing work | `metaforge://twin/requirements/<project_id>`, `metaforge://twin/brief/<project_id>` | all |
| Parts, values and which rail each sits on | `kicad.get_pin_mapping` | electronics |
| Part numbers already chosen | BOMItems via `twin.find_by_property` (`property: "mpn"`) / `twin.get_node` | all |
| Datasheet figures | `knowledge.search`; `web.search` then `web.fetch`; `knowledge.extract` only if listed | all / core / no profile |
| Keep a datasheet for next time | `knowledge.ingest` | core |
| Record the budget | `twin.record_document`, `twin.record_decision` | core / all |
| Budget entity and evidence | `twin.record_engineering_entity`, `twin.record_evidence`, only if listed | not on the dev server |

## Inputs you need before you start

Ask the user for anything you cannot read from the design or a cited
datasheet. Never fill a current, a capacity or a margin with a typical value
of your own.

| Input | Why it matters | Example |
|---|---|---|
| The rails | Each rail is budgeted separately | `VBAT 7.4 V`, `5V0`, `3V3` |
| What feeds each rail, and its rated output | Capacity is the limit you compare against | "3V3 from an AP2112K, 600 mA rated" |
| Upstream chain | A rail's load is its regulator's input load upstream (efficiency for a buck, same current for an LDO) | "3V3 LDO is fed from 5V0" |
| Each load's worst-case draw and its source | The budget is only as good as these numbers | "ESP32 TX peak 240 mA, datasheet table 5-4" |
| Operating case | Peak, typical and sleep give very different answers | "all radios transmitting at once" |
| Margin or derating rule | Decides pass or fail | "load <= 80 % of rated output" |
| The acceptance limit, if a requirement exists | What result counts as a pass for the requirement | "total <= 2 W from VBAT" |

If a figure is not in any datasheet you can read, the honest entry is
"unknown". A rail with an unknown load cannot pass; it is "not established".

## Procedure

### 1. Start from what the project already has

1. Read `metaforge://twin/requirements/<project_id>` for power or current
   requirements and their limits, and `metaforge://twin/brief/<project_id>`
   for the schematic, BOM and any earlier budget. Reuse an earlier budget's
   sourced figures rather than asking again; check they still match the BOM.
2. `twin.constraint_violations` with `project_id` shows anything already
   failing that touches power.

### 2. Build the load list

1. Get the parts and their rails in one call: `kicad.get_pin_mapping` with
   `schematic_file` (a path the KiCad adapter can read; see `run_erc` for
   staging). It returns each component's `reference`, `value`, `footprint`
   and `pins`, each pin with its `name`, `type` and `net`. A part whose power
   pin sits on net `+3V3` loads the 3V3 rail. `component_filter` (a reference
   prefix such as `U`) keeps it short. Do not use `kicad.export_bom` for
   this: it returns only counts and a file path inside the adapter container,
   not the part list.
2. Get exact part numbers from the project's BOMItems where they exist
   (`twin.find_by_property` with `property: "mpn"`, or the brief). A value
   such as `ESP32-S3` is not enough to pick the right datasheet table.
   Passives and connectors usually draw nothing worth budgeting; LEDs,
   pull-ups held low and external modules on a connector do, so ask the user
   about anything off-board.
3. For each load, get the worst-case current for the operating case the user
   chose:
   - `knowledge.search` for an already-ingested datasheet (cite
     `source_path` and heading).
   - Otherwise `web.search` for the datasheet, `web.fetch` to read it (watch
     for the in-band note that later PDF pages were not read), and
     `knowledge.ingest` it if the user wants it kept.
   - If `knowledge.extract` is in your tool list, it returns typed values
     with a citation and an `extraction_method`; treat `llm_inferred` as
     weaker than `verbatim` and say which you used.
   - Use maximum, not typical, unless the user explicitly asks for typical.
   - Note the source of every number: document, page or table.

### 3. Do the arithmetic

For each rail, in a table you show the user:

1. Sum the worst-case currents of its loads (convert power to current at the
   rail voltage where a datasheet gives power).
2. Compare with the capacity: rated output current of its source. Apply the
   user's margin: allowed = rated x derating, or required headroom.
3. Headroom = allowed minus load, in mA and as a percentage. Pass when load
   <= allowed.
4. Propagate upstream: an LDO draws its output current plus its quiescent
   current from its input rail; a switching regulator draws
   (Vout x Iout) / (efficiency x Vin) from its input, using the efficiency at
   that load from its datasheet. Add that to the upstream rail and repeat.
5. Total power at the source, if a requirement limits it.
6. Name the worst rail: smallest headroom, or any rail that fails.

Double-check units (mA vs A, mW vs W) and show the working. Every number in
the table needs a source or the word "unknown".

### 4. Record the budget

Only when the user wants it kept, or a flow phase needs it:

1. `twin.record_document` with `document_type: "documentation"`, a `name`
   like `Power budget, FC-Main, peak TX case`, and `content` holding the
   operating case, margin rule, the per-rail table with sources, the worst
   rail, the verdict, and every unknown. Pass `source_part_node_ids` with the
   schematic or BOM work product ids it was built from, and `depends_on` with
   the constraint set's item reference when it answers a requirement.
2. `twin.record_decision` for choices a reviewer could disagree with (peak vs
   typical, a derating figure, ignoring an off-board load): `title`,
   `rationale`, and `alternatives` with `option` and `reason_rejected`.
3. If `twin.record_engineering_entity` is in your tool list and the user
   wants a tracked budget, record `entity_type: "budget"` with `extra`
   holding `metric`, `unit`, `system_total` and per-rail `allocations`. If
   `twin.record_evidence` is listed, record `evidence_type: "calculation"`
   with `producer` naming your client (not a MetaForge tool, since none
   produced it), `inputs` holding the sourced load list and `result` the
   per-rail table. If neither is listed, tell the user the budget is stored
   as a document only and is not linked to the requirement.

### 5. Report

- Verdict per rail and overall, with the margin rule
- The worst rail and its headroom
- Every unknown or weak figure, and what would close it
- The fact that this is a hand calculation from cited figures, not a tool
  result
- What was recorded, with node ids; then re-read the requirements resource
  and give each power requirement's status as shown

## Checks before you report

- [ ] Every load and capacity has a cited source, or is marked unknown
- [ ] Maximum figures used, or the user chose typical and you said so
- [ ] Margin rule came from the user
- [ ] Units consistent; working shown
- [ ] Upstream rails include regulator input current and efficiency loss
- [ ] Off-board loads asked about
- [ ] Nothing claimed as a MetaForge tool result

## Failure handling

| Symptom | Likely cause | What to do |
|---|---|---|
| `-32001` naming `kicad` | KiCad adapter down | Tell the user; build the load list from the twin BOM or ask for it. |
| `kicad.get_pin_mapping` returns no components or no pins | File not readable by the adapter, or the netlist export inside it failed | Stage the schematic or ask for a path; otherwise ask the user which rail feeds each part. |
| Datasheet figure not found | Not ingested, PDF page limit, or the vendor does not publish it | Mark unknown; ask the user; do not estimate. |
| Two datasheets disagree | Different revisions or package variants | Use the one matching the exact MPN; record the choice with `twin.record_decision`. |
| `web.*` or `twin.record_document` missing | Connection is on `electronics` | Ask for `core` or no profile; report what was not done. |
| A record call held for approval | Writes need a person on this connection | Tell the user where it waits; do not retry or reword. |

## Limits

- Static worst-case sum only. No transients, inrush, brown-out, ripple or
  regulator thermal derating unless the user supplies those figures.
- No tool checks your arithmetic; show it so a person can.
- The budget is valid for the BOM and schematic revision it was built from.
  A part change makes it stale; re-run it.
