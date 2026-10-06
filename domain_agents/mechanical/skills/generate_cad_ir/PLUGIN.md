---
description: Build a featured CAD part step by step (body, sketches, pads, pockets, booleans, fillets, revolves, patterns) as an ordered feature list, execute it through the FreeCAD session tools or one CadQuery script, then export a correctly named STEP and commit it to the twin. Use when the user wants a real part with holes, pockets, cut-outs, bosses, rounded edges, revolved or patterned features at stated positions, or when a simple parametric primitive from generate_cad cannot represent the part.
---

# generate_cad_ir

Model a part the way an engineer would: an ordered list of named features,
each referring to earlier ones by id, executed against a real CAD kernel and
ending in one exportable solid. The feature list (the Design IR) is your plan
and your audit trail; the server's session tools carry it out one call at a
time. The result is a STEP whose product name is the part's name and a
committed, measured `cad_model` in the twin.

## When to use it

- "A 100 x 60 x 8 mm plate with four 5.5 mm holes 6 mm in from each corner."
- "A flange: 80 mm OD disc, 10 mm thick, 6 holes on a 60 mm bolt circle."
- "Pocket a 40 x 20 x 3 mm recess into the top of the housing."
- "Revolve this shaft profile and chamfer the ends."

Not for:

- A plain plate, cylinder, L-bracket or open box with no extra features:
  `generate_cad` is one call.
- Several parts placed together: build each part here, commit each, then
  `create_assembly`.
- Changing a committed part the user wants reviewed first: run
  `decide_sketch_needed` before you open a session.

## Tools and profile

There are two execution paths. Pick by what the connection serves.

**Path A, FreeCAD session** (preferred: names and colours land in the STEP).

| Step | Tool | Served on |
|---|---|---|
| Open / close | `freecad.open_session`, `freecad.close_session` | `mechanical_product` |
| Body, sketch, pad, pocket | `freecad.create_body`, `freecad.create_sketch`, `freecad.pad_sketch`, `freecad.pocket_sketch` | `mechanical_product` |
| Primitives and CSG | `freecad.create_primitive`, `freecad.boolean`, `freecad.fillet`, `freecad.chamfer`, `freecad.transform_object` | `mechanical_product` |
| Inspect | `freecad.measure`, `freecad.describe_session` | `mechanical_product` |
| Export | `freecad.export_model` | `mechanical_product` |
| Revolve, loft, sweep, patterns, mirror, shell, edge fillet or chamfer | `freecad.revolve_sketch`, `freecad.loft_sketches`, `freecad.sweep_sketch`, `freecad.linear_pattern`, `freecad.polar_pattern`, `freecad.mirror_feature`, `freecad.shell_solid`, `freecad.fillet_edges`, `freecad.chamfer_edges` | no named profile: only a connection with no `profile` serves them |
| Commit | `twin.commit_geometry` | `mechanical`, `mechanical_product`, `robotics` |

**Path B, one CadQuery script** (works on the `mechanical` profile).

| Step | Tool | Served on |
|---|---|---|
| Build, measure, export | `cadquery.execute_script` | `mechanical` |
| Inspect | `cadquery.get_properties`, `freecad.describe_step_file` | `mechanical` |
| Commit | `twin.commit_geometry` | `mechanical` |

Call `health.check` first. The active profile it reports tells you which path you have;
`unreachable_adapters` tells you whether `freecad` or `cadquery` is down. If
the features you need are not served, tell the user which profile (or a
no-profile connection) would serve them rather than dropping features.

## Inputs you need before you start

Ask the user. Do not fill these in: a missing dimension in
`freecad.create_primitive` silently becomes a 10 mm default.

| Input | Why it matters | Example |
|---|---|---|
| Part name | Body label, STEP product name and twin item name | `Pump Flange` |
| Base form and every dimension (mm) | Each feature needs its own numbers | disc 80 OD x 10 |
| Each feature's position and size | Holes, pockets, bosses need coordinates | 6 x 6.5 mm holes on 60 mm PCD |
| Which edges to round, and radius | Edge fillets need named edges or "all edges" | "all outer edges, R1" |
| Material | Mass on the node, colour in the STEP | `aluminum_6061` |
| Project | Where the item lives | project name or id |

## Procedure

### 1. Write the feature list first

Before any tool call, write the part as ordered entities with ids, for
example: `body1` create_body, `sk1` sketch on `body1` XY, `pad1` pad `sk1`
depth 10, `sk2` sketch of the holes at offset 10, `pk1` pocket `sk2` depth
10. Rules:

- Every reference points to an earlier id. Order is execution order.
- The last entity must have a solid shape. A list ending in create_body, a
  sketch, an assembly or a joint has nothing to export.
- One tool per boolean: subtracting three cylinders is three boolean
  entities, chained.
- Show the user the list when it embodies a judgement call (a hole pattern
  you laid out from their description). Confirm before building.

### 2a. Execute on FreeCAD (Path A)

1. `freecad.open_session` (optional `name`). Keep `session_id`.
2. For each entity, call its tool. Each returns an `obj_id`; map entity id to
   `obj_id` as you go. Translation table:

| Entity | Tool | Arguments that matter |
|---|---|---|
| create_body | `freecad.create_body` | `name`: the part name |
| sketch | `freecad.create_sketch` | `body_id`, `plane` XY/XZ/YZ, `offset`, `elements` |
| pad | `freecad.pad_sketch` | `body_id`, `sketch_id`, `length` (the depth), `reversed`, `midplane` |
| pocket | `freecad.pocket_sketch` | `body_id`, `sketch_id`, `depth`, `reversed` |
| revolve | `freecad.revolve_sketch` | `body_id`, `sketch_id`, `axis` V or H, `angle` |
| create_primitive | `freecad.create_primitive` | `kind`, `parameters` (all of them), `name` |
| boolean | `freecad.boolean` | `obj_a` (base), `obj_b` (tool), `operation`, `name` |
| fillet / chamfer (whole solid) | `freecad.fillet` / `freecad.chamfer` | `obj_id`, `radius` / `distance`, `name` |
| edge fillet / chamfer on a body | `freecad.fillet_edges` / `freecad.chamfer_edges` | `body_id`, `radius` / `size`, `edges` |
| pattern | `freecad.linear_pattern` / `freecad.polar_pattern` | `body_id`, `feature_id`, `count`, `spacing` or `angle`, `axis` |
| mirror | `freecad.mirror_feature` | `body_id`, `feature_id`, `plane` |
| shell | `freecad.shell_solid` | `body_id`, `thickness`, `faces` |
| move | `freecad.transform_object` | `obj_id`, `position` [x,y,z], `rotation` {axis, angle_deg} |

   Sketch `elements` use `type` plus either flat keys or grouped points:
   circle `cx`, `cy`, `r`; rectangle `x`, `y`, `width`, `height`; line `x1`,
   `y1`, `x2`, `y2`; arc `cx`, `cy`, `r`, `start_angle`, `end_angle`.
   Edge names in `edges` are FreeCAD topology names such as Edge3; leave
   `edges` empty only when the user wants every edge treated.
3. After the last feature, `freecad.measure` on its `obj_id`. Check
   `volume_mm3` and the bounding box against the user's sizes.
4. Pick what to export. If the last feature belongs to a body (pad, pocket,
   revolve, pattern, edge fillet), export the **body's** `obj_id`: the body
   carries the part name, the tip feature carries an operation name. If the
   last entity is a primitive or boolean result, export it, and make sure you
   gave that final call `name` = the part name (otherwise the STEP product is
   called subtract_result or filleted).
5. `freecad.export_model` with `session_id`, `obj_id` and, only if the user
   gave one, `material` (or `color`). An unknown material stays uncoloured;
   never invent a colour.
6. `twin.commit_geometry` with the **same** `session_id` and `obj_id`, plus
   `name`, `project_id`, `parameters` (the driving values, e.g.
   `{"thickness_mm": 10}`) and `properties` from `freecad.measure`. Do not
   paste the base64; the server already holds the export.
7. `freecad.close_session`, even if a step failed.

Never fall back to writing your own STEP export in `freecad.execute_code`:
a hand-written export collapses labels and assembly structure, which is the
defect `freecad.export_model` exists to avoid.

### 2b. Execute as one CadQuery script (Path B)

1. Translate the feature list into one script that assigns the final
   Workplane to `result`. Keep entity ids as variable names.
2. `cadquery.execute_script` with `script` and a unique `output_path` such as
   output/pump_flange_r1.step.
3. Check `volume_mm3` and `bounding_box` against the user's sizes;
   `freecad.describe_step_file` on `cad_file` gives a second reading.
4. `twin.commit_geometry` with `name`, `file_path` = the returned `cad_file`,
   `project_id`, `source_tool: "cadquery.execute_script"`, `script_source`
   (the script, kept as the part's source of truth), `parameters` and
   `properties`.

### 3. Read the commit and report

`node_id`, `model_url`, `item_key`, `revision`, `project_linked`.
`already_committed: true` means this exact geometry under this name already
existed: nothing new was created, do not commit again. Record any modelling
choice a reviewer could disagree with using `twin.record_decision`
(`title`, `rationale`, `alternatives`).

## Checks before you report

- [ ] Every dimension and position came from the user
- [ ] The feature list ends in a solid, and every reference resolved
- [ ] Measured size and volume match the intent
- [ ] Exported the body (or a final solid named after the part)
- [ ] Committed by reference (session ids, or `file_path`), not base64
- [ ] Colour claimed only if `material` or `color` was given and recognised
- [ ] FreeCAD session closed

## Failure handling

| Symptom | Likely cause | What to do |
|---|---|---|
| `-32001` naming `freecad` or `cadquery` | Adapter container down | Tell the user. Offer the other path once; do not loop. |
| A feature tool is not in your list | Profile does not serve it | Name the tool and the connection that serves it; do not drop the feature silently. |
| "nothing to export" or no shape | Exported a sketch, body with no features, or joint | Export the body after its first solid feature, or the final solid. |
| Fillet or shell fails | Radius too large for an edge, or wrong edge/face names | Report; ask the user for a smaller radius or the exact edges. |
| commit says obj_id without session_id | Retried with half the reference | Pass both, exactly as given to `freecad.export_model`. |
| STEP product name is an operation name | Exported the tip feature or an unnamed result | Re-export the body, or redo the final op with `name`. |
| Commit held for approval | Writes need a person | Tell the user where it waits; do not retry or reword. |

## Limits

- One exportable solid per feature list. Assemblies go through
  `create_assembly`.
- Path A on the `mechanical_product` profile has no revolve, loft, sweep,
  pattern, mirror, shell or edge-level fillet/chamfer; those need a
  connection with no profile. Path B can express them in CadQuery, but cannot
  use FreeCAD edge names (Edge3) as selectors.
- Editing means resubmitting the whole feature list; there is no incremental
  re-run of one feature.
- The CadQuery path writes no part label or colour into the STEP; the twin
  item still takes the committed `name`.
