---
name: run_erc
description: Run KiCad's Electrical Rules Check on a schematic, sort the violations into errors and warnings, judge pass or fail on errors, and record the result in the twin pinned to the schematic it checked. Use when the user asks to run ERC, check a schematic for electrical errors, asks whether the schematic is clean before layout, or when a requirement or flow gate needs ERC evidence.
domain: electronics
---

# run_erc

Check a KiCad schematic for electrical rule violations (unconnected pins,
conflicting outputs, missing power drivers, ...) with KiCad's own ERC, and
leave behind a result a reviewer can trust: which file and revision was
checked, every error and warning, and a pass or fail that follows a stated
rule.

## When to use it

- "Run ERC on the flight controller schematic."
- "Is the schematic clean enough to start layout?"
- "Why does KiCad complain about the 3V3 net?"
- A design-flow phase or requirement asks for ERC evidence before the
  schematic is accepted.

Not for: the PCB layout (use `run_drc` and `kicad.run_drc`), a bill of
materials (`kicad.export_bom`), the pin-to-net table for firmware
(`kicad.get_pin_mapping`), or a power budget (`check_power_budget`). ERC
cannot fix anything: MetaForge's KiCad support is read-only in this phase.

## Tools and profile

`kicad.*` is served on the **`electronics`** profile. Staging a file out of
the twin and writing a document are not: `twin.stage_work_product_file` is on
`mechanical`, `simulation` and `robotics`, and `twin.record_document` is on
`core`. A connection with no `?profile=` serves all of them. If a tool below
is missing, call `health.check`, read its `profile` field, and tell the user
which profile (or no profile) they need, rather than skipping the step.

| Step | Tool | Profile |
|---|---|---|
| Check the adapter is up | `health.check` | all |
| Find the schematic work product | `twin.get_node`, `twin.find_by_property`, `metaforge://twin/brief/<project_id>` | all |
| Put the file where KiCad can read it | `twin.stage_work_product_file` | mechanical, simulation, robotics, no profile |
| Run ERC | `kicad.run_erc` | electronics |
| Record the result | `twin.record_document` | core |
| Record a waiver-worthy choice | `twin.record_decision` | all |
| Link to a requirement | `twin.record_evidence` (only if listed) | not on the dev server |

## Inputs you need before you start

Ask the user for anything missing. Do not guess a file or pick between two
candidates yourself.

| Input | Why it matters | Example |
|---|---|---|
| Which schematic | The result is only valid for that file and revision | the root sheet of `FC-Main`, or a twin node id |
| Where the file lives | `kicad.run_erc` runs inside the KiCad adapter container and can only open paths on its filesystem | a twin work product to stage, or a path under the shared adapter workspace |
| The other sheets and the project file | A hierarchical schematic needs its sub-sheets next to the root; KiCad keeps the ERC pin-conflict settings in the project (.kicad_pro) file | "the whole eda/kicad folder" |
| What counts as a pass | Default rule: zero errors, warnings reported | "warnings must also be zero for release" |
| Known accepted violations | Some warnings are deliberate (a test point, a no-connect) | "U3 pin 7 is intentionally floating" |

## Procedure

### 1. Check the connection

Call `health.check`. If `status` is `degraded` and `unreachable_adapters`
names `kicad`, stop and tell the user: every `kicad.*` call will fail with
`-32001` until the KiCad adapter container is running.

### 2. Find the schematic and get a path KiCad can open

1. If the schematic was imported into the project (dashboard or gateway
   import creates a `schematic` work product), find it from
   `metaforge://twin/brief/<project_id>` or with `twin.get_node`. Note the
   node id and its revision: the result is pinned to both.
2. Call `twin.stage_work_product_file` with `node_id`. It writes the file to
   the shared adapter workspace, which the KiCad adapter mounts, and returns
   `file_path`. Use that path as-is.
3. If the schematic is not in the twin, ask the user for a path the KiCad
   adapter can read. A path in the user's own repo (for example
   eda/kicad/main.kicad_sch on their laptop) is not visible to the adapter
   container unless that folder is mounted into it. Do not invent a path.
4. Staging copies one file. If the design is hierarchical or relies on
   project-file ERC settings, tell the user the check ran on that file alone
   and may differ from what KiCad shows them locally.

### 3. Run ERC

Call `kicad.run_erc` with:

- `schematic_file`: the path from step 2
- `severity_filter`: `"all"`. Always start with `all`. Filtering to `error`
  hides the warnings, and the tool's `passed` flag is computed after the
  filter.

Read from the result: `total_violations`, `errors`, `warnings`, `passed`, and
`violations`. Each violation has `rule_id` (KiCad's type, for example
`pin_not_connected`), `severity`, `message`, `sheet`, `component` and `pin`.
`component` and `pin` are pulled out of KiCad's item descriptions on a best-
effort basis and can be empty; quote `message` when they are.

### 4. Judge the result

- **The tool's `passed` is stricter than this skill's rule.** The tool sets
  `passed: true` only when there are no violations of any severity. This
  skill passes on **zero errors**, with warnings reported. Compute the
  verdict from `errors` yourself and state which rule you applied. If the
  user gave a stricter rule (zero warnings), use theirs.
- If `total_violations` is not `errors + warnings`, KiCad reported a
  violation with another severity. List those separately; do not drop them.
- Group violations by `rule_id`. Thirty `pin_not_connected` on one connector
  is one finding, not thirty.
- For each error, say what it means electrically and which net or part is
  involved. Do not propose a fix as fact unless the message makes it
  unambiguous; the user owns the schematic.
- A warning the user said is deliberate stays in the report, marked as
  accepted by them.

### 5. Record the result

Only when the user asked for the result to be kept, or a flow phase needs it:

1. Call `twin.record_document` with `document_type: "documentation"`, a
   `name` like `ERC, FC-Main, rev 4`, `content` holding the verdict, the rule
   used, the counts, every error and a grouped list of warnings, and
   `source_part_node_ids: [<schematic node id>]` so the record links to the
   schematic. Documentation is always stored as markdown.
2. If `twin.record_evidence` is in your tool list, also record evidence:
   `evidence_type` as the requirement's `expected_evidence` names it,
   `producer: {"tool": "kicad.run_erc"}`, `inputs` with the file and filter,
   `result` set to the tool's own output unchanged, `supports` (or
   `contradicts`) naming the requirement, and `valid_against` naming the
   schematic work product. If it is not listed, tell the user the link to the
   requirement was not recorded on this connection.
3. When the user accepts a violation instead of fixing it, record that with
   `twin.record_decision`: `title`, `rationale`, and `alternatives` (for
   example fixing it with a no-connect flag) with why each was rejected.

### 6. Report

- Verdict, the rule used, and the file and revision checked
- Counts: errors, warnings, any other severity
- Each error with its net, part and sheet; warnings grouped by rule
- Anything that limits the result (single file staged, missing sub-sheets)
- What was recorded, with node ids

## Checks before you report

- [ ] `health.check` showed `kicad` reachable, or you reported that it is not
- [ ] The file checked is the one the user meant, at a known revision
- [ ] Ran with `severity_filter: "all"`
- [ ] Verdict computed from `errors`, with the rule stated
- [ ] `total_violations` equals `errors + warnings`, or the rest are listed
- [ ] Hierarchy and project-file caveats stated when only one file was staged
- [ ] Nothing recorded the user did not ask for

## Failure handling

| Symptom | Likely cause | What to do |
|---|---|---|
| `-32001` naming `kicad` | KiCad adapter container is down | Tell the user; do not retry in a loop. |
| kicad-cli not found | The adapter image has no KiCad 8 | Report it; ERC cannot run on this server. |
| File not found, or kicad-cli exits non-zero with no report | Path is not on the adapter's filesystem, or not a .kicad_sch | Stage it with `twin.stage_work_product_file`, or ask the user for an adapter-visible path. |
| Sub-sheet errors or a near-empty result on a big design | Only the root sheet was staged | Say the check is incomplete; ask for the full project folder in the workspace. |
| Timed out | Very large schematic (limit is about 120 s) | Report it; do not loop. |
| `kicad.run_erc` not in your list | Connection is not on `electronics` | Check `health.check` `profile`; ask the user to reconnect. |
| `twin.stage_work_product_file` or `twin.record_document` not in your list | `electronics` profile does not serve them | Ask for a no-profile connection, or report the result without recording it and say so. |
| A record call held for approval | Writes need a person on this connection | Tell the user where it waits; do not retry or reword. |

## Limits

- Detection only. MetaForge cannot edit the schematic in this phase.
- Uses KiCad's built-in ERC; no custom rule file is applied.
- Checks connectivity rules only: it says nothing about component values,
  power budget, signal integrity or whether the circuit does what was
  intended.
- A result belongs to the revision it was run on. After the schematic
  changes, run it again.
