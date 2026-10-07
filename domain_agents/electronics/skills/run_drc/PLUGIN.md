---
description: Run KiCad's Design Rules Check on a PCB layout, sort the violations and unrouted connections, judge pass or fail, and record the result in the twin pinned to the board revision it checked. Use when the user asks to run DRC, check a board for clearance, track width, courtyard or unrouted-net problems, asks whether a layout is ready for Gerbers or fab, or when a flow gate needs DRC evidence.
---

# run_drc

Check a KiCad PCB layout against its design rules with KiCad's own DRC, and
leave behind a result a reviewer can trust: which board and revision was
checked, which rules applied, every error, warning and unrouted connection,
and a pass or fail that follows a stated rule.

## When to use it

- "Run DRC on the FC board."
- "Is the layout ready to export Gerbers?"
- "Are there any clearance violations on the power section?"
- A design-flow phase or requirement asks for DRC evidence before the board
  is released to manufacture.

Not for: the schematic (use `run_erc` and `kicad.run_erc`), exporting
manufacturing files (`kicad.export_gerber`, after DRC is clean), impedance or
signal-integrity analysis (no tool on this server does that), or fixing the
layout: MetaForge's KiCad support is read-only in this phase.

## Tools and profile

`kicad.*` is served on the **`electronics`** profile. `twin.stage_work_product_file`
is on `mechanical`, `simulation` and `robotics`, and `twin.record_document`
is on `core`. A connection with no `?profile=` serves all of them. If a tool
below is missing, call `health.check`, read its `profile` field, and tell the
user which profile (or no profile) they need.

| Step | Tool | Profile |
|---|---|---|
| Check the adapter is up | `health.check` | all |
| Find the board work product | `twin.get_node`, `metaforge://twin/brief/<project_id>` | all |
| Put the file where KiCad can read it | `twin.stage_work_product_file` | mechanical, simulation, robotics, no profile |
| Run DRC | `kicad.run_drc` | electronics |
| Manufacturing export, only once clean | `kicad.export_gerber` | electronics |
| Record the result | `twin.record_document` | core |
| Record an accepted violation | `twin.record_decision` | all |
| Link to a requirement | `twin.record_evidence` (only if listed) | not on the dev server |

## Inputs you need before you start

Ask the user for anything missing. Do not pick a board or a rule set for them.

| Input | Why it matters | Example |
|---|---|---|
| Which board | The result is only valid for that file and revision | `FC-Main` .kicad_pcb, or a twin node id |
| Where the file lives | `kicad.run_drc` runs inside the KiCad adapter container and can only open paths on its filesystem | a twin work product to stage, or a path in the shared adapter workspace |
| Which rules apply | KiCad keeps the board's constraints and net classes in the project file (.kicad_pro) and custom rules in a .kicad_dru file next to the board; a board staged alone runs without them | "the fab's 6/6 mil rules are in the project file" |
| What counts as a pass | Default rule: zero errors and zero unrouted connections, warnings reported | "warnings must be zero for release" |
| Known accepted violations | Some are deliberate (a net tie, a silkscreen overlap) | "the antenna keepout warning is expected" |

## Procedure

### 1. Check the connection

Call `health.check`. If `unreachable_adapters` names `kicad`, stop and tell
the user: every `kicad.*` call will fail with `-32001` until the KiCad
adapter container is running.

### 2. Find the board and get a path KiCad can open

1. If the board was imported into the project (dashboard or gateway import
   creates a `pcb_layout` work product), find it in
   `metaforge://twin/brief/<project_id>` or with `twin.get_node`. Note the
   node id and revision: the result is pinned to both.
2. Call `twin.stage_work_product_file` with `node_id`. It writes the file
   into the shared adapter workspace, which the KiCad adapter mounts, and
   returns `file_path`.
3. If the board is not in the twin, ask the user for a path the adapter can
   read. A file in the user's own repo is not visible to the container unless
   that folder is mounted into it. Do not invent a path.
4. Staging copies one file. If the board's rules live in its .kicad_pro or a
   .kicad_dru, tell the user the check ran without them unless those files
   sit beside the board in the workspace. A DRC that ran on default rules is
   not evidence that the fab's rules are met.

### 3. Run DRC

Call `kicad.run_drc` with:

- `pcb_file`: the path from step 2
- `severity_filter`: `"all"`. Filtering hides findings from the list. It
  does not change `passed`, which counts every error.
- `rule_set` (optional): custom design rules, either a path to a
  `.kicad_dru` file on the adapter or the rules text itself. KiCad has no
  rules flag, so the tool runs DRC on a copy of the board with these rules
  as its `.kicad_dru`. The result's `rule_set_applied` says whether that
  happened; only say custom rules were applied when it is `true`.

Read from the result: `total_violations`, `errors`, `warnings`,
`unconnected_items`, `rule_set_applied`, `passed`, and `violations`. Each violation has
`rule_id` (KiCad's type, for example `clearance` or `track_width`),
`severity`, `message`, and `location` as an object with `x`, `y` (board
coordinates in KiCad's report units, millimetres by default) and `layer`.

### 4. Judge the result

- `unconnected_items` counts nets KiCad considers unrouted. They are not in
  `violations` and not in `errors`, but a board with any is not ready for
  manufacture. Report the count every time.
- `passed` is true with zero error-severity violations and zero unconnected
  items, counted before any severity filter. Warnings do not fail it; report
  them. This is the same rule the `run_drc` skill applies. If the user set a
  stricter one (zero warnings), apply theirs and say so.
- If `total_violations` is not `errors + warnings`, list the violations with
  another severity separately.
- Group by `rule_id` and layer; give the location of each error so the user
  can find it in KiCad.
- Explain what each error means for fabrication or assembly. Do not present a
  layout fix as fact; the user owns the board.

### 5. Record the result

Only when the user asked for it, or a flow phase needs it:

1. `twin.record_document` with `document_type: "documentation"`, a `name`
   like `DRC, FC-Main, rev 7`, `content` holding the verdict and rule, which
   rules were in effect (project file present or not), counts, unrouted
   count, each error with location, warnings grouped, and
   `source_part_node_ids: [<board node id>]`.
2. If `twin.record_evidence` is in your tool list, record evidence with
   `producer: {"tool": "kicad.run_drc"}`, `inputs` (file, filter, whether the
   project rules were present), `result` set to the tool's own output
   unchanged, `supports` or `contradicts` naming the requirement, and
   `valid_against` naming the board work product. If it is not listed, tell
   the user the requirement link was not recorded on this connection.
3. A violation the user accepts rather than fixes goes to
   `twin.record_decision` with `title`, `rationale` and `alternatives`.

### 6. Report

- Verdict, rule used, board and revision, and which design rules were in
  effect
- Errors (with location and layer), warnings grouped, unrouted connections
- Whether the board is ready for `kicad.export_gerber`, and if not, what
  blocks it
- What was recorded, with node ids

## Checks before you report

- [ ] `kicad` reachable, or you said it is not
- [ ] The board checked is the one the user meant, at a known revision
- [ ] You know whether the project rules were present; you said so
- [ ] Ran with `severity_filter: "all"`
- [ ] `unconnected_items` reported, and counted against the verdict
- [ ] `total_violations` equals `errors + warnings`, or the rest are listed
- [ ] Custom rules claimed only when `rule_set_applied` is `true`

## Failure handling

| Symptom | Likely cause | What to do |
|---|---|---|
| `-32001` naming `kicad` | KiCad adapter container is down | Tell the user; do not retry in a loop. |
| kicad-cli not found | The adapter image has no KiCad 8 | Report it; DRC cannot run on this server. |
| File not found, or kicad-cli fails with no report | Path not on the adapter's filesystem, or not a .kicad_pcb | Stage with `twin.stage_work_product_file`, or ask for an adapter-visible path. |
| Far fewer violations than KiCad shows the user | Board ran without its .kicad_pro / .kicad_dru rules | Say so; ask for the project files in the workspace and run again. |
| Timed out | Large board (limit is about 120 s) | Report it; do not loop. |
| `kicad.run_drc` not in your list | Connection is not on `electronics` | Check `health.check` `profile`; ask the user to reconnect. |
| Staging or recording tools not in your list | `electronics` does not serve them | Ask for a no-profile connection, or report without recording and say so. |
| A record call held for approval | Writes need a person on this connection | Tell the user where it waits; do not retry or reword. |

## Limits

- Detection only; MetaForge cannot edit the layout in this phase.
- A `rule_set` replaces any `.kicad_dru` beside the board for that run; it
  is not merged with it.
- Standard DRC only: no controlled-impedance, length matching beyond what
  the board's own rules encode, thermal or current-capacity analysis.
- A result belongs to the revision it ran on. After the board changes, run
  it again before exporting Gerbers.
