---
name: check_power_budget
description: Check an electronics design's power budget by summing worst-case load on each supply rail, comparing it against what the rail can deliver with the user's margin, flagging any rail over budget, and recording the sourced budget in the twin. Use when the user asks whether a regulator or battery can supply the board, asks for a power or current budget per rail, asks how much headroom a rail has, or when a power or current requirement needs analysis evidence.
domain: electronics
---

# check_power_budget

Answer "can each supply rail carry its load, with margin?" for a real design,
and leave behind a budget a reviewer can audit: every load with the source of
its number, every rail's capacity with its source, the margin rule, and a
per-rail pass or fail.

**`power.check_budget` does the arithmetic; you supply the figures.** The
tool sums each rail's worst-case load, carries regulator input current
upstream, applies the derating rule, and returns a per-rail pass, fail or
not established. It reads nothing from the design: every rail rating and
every load comes from you, read out of the design and its datasheets with
real tools, each with its source. A figure you do not pass is unknown, and
the tool never fills one in.

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
| The budget arithmetic | `power.check_budget` | electronics |
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

### 3. Compute the budget

Call `power.check_budget` with:

- `rails`: one object per rail with `name`, `voltage_v`, `source_kind`
  (`supply` for a battery or external input, `ldo`, or `switching`),
  `rated_current_ma` (the source's rated output), and `source` (where the
  rating came from). A regulator also needs `input_rail`; a switching one
  needs `efficiency` at that load (0 to 1); an LDO takes `quiescent_ma`.
- `loads`: one object per load with `name`, `rail`, either `current_ma` or
  `power_mw` (converted at the rail voltage), and `source`. Leave both
  figures out for a load you could not source; it stays unknown.
- `derating`: the user's margin rule as a share of rated output, e.g. `0.8`
  for "load <= 80 % of rated".

It returns `verdict` (`pass`, `fail` or `not_established`), `passed`,
`worst_rail`, `source_power_mw`, and per rail `load_ma` (own loads plus the
input current of regulators it feeds: an LDO's output plus quiescent, a
switching regulator's `Vout x Iout / (efficiency x Vin)`), `allowed_ma`
(rated x derating), `headroom_ma`, `headroom_pct`, `status`, `unknowns` and
`notes`. A rail whose known load already exceeds its allowance fails even
with unknowns; otherwise any unknown leaves it `not_established`.

Show the user the per-rail table from the result, with the source of every
figure you passed in. If the tool is not in your list, the same rules apply
by hand; say the sum is yours, not a tool's.
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
   with `producer: "power.check_budget"`, `inputs` holding the sourced rails
   and loads you passed, and `result` the tool's per-rail table. If neither is listed, tell the user the budget is stored
   as a document only and is not linked to the requirement.

### 5. Report

- Verdict per rail and overall, with the margin rule
- The worst rail and its headroom
- Every unknown or weak figure, and what would close it
- That `power.check_budget` computed it from the cited figures you gave it
  (or, if the tool was unavailable, that the sum is yours)
- What was recorded, with node ids; then re-read the requirements resource
  and give each power requirement's status as shown

## Checks before you report

- [ ] Every load and capacity has a cited source, or is marked unknown
- [ ] Maximum figures used, or the user chose typical and you said so
- [ ] Margin rule came from the user
- [ ] Units consistent (`current_ma`, `power_mw`, `voltage_v`)
- [ ] Every regulator has its `input_rail`; every switching one its `efficiency`
- [ ] Off-board loads asked about
- [ ] Unknowns left unknown, not filled with a typical value

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
- The tool checks the arithmetic, not the figures: a wrong datasheet
  number gives a wrong budget. Cite every one so a person can check it.
- The budget is valid for the BOM and schematic revision it was built from.
  A part change makes it stale; re-run it.
