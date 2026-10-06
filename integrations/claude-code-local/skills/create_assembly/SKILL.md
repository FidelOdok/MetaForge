---
name: create_assembly
description: Combine already-built CAD parts into one named multi-part assembly STEP at user-given positions, check that the parts, names and placements came out right, and commit the assembly to the twin linked to its part items. Use when the user asks to assemble, put together, stack or mount committed parts (for example a bracket on a plate, a gripper from its fingers and base, an arm from its links), or a design phase needs an assembly cad_model built from part cad_models.
domain: mechanical
---

# create_assembly

Place committed parts in one frame and save them as a single STEP assembly
that keeps each part as a named component, then commit it as a `cad_model`
whose twin item links to every part item it contains. A reviewer then sees
which parts make up the assembly, where each sits, and can open each part on
its own.

## When to use it

- "Assemble the motor mount plate and the two side brackets."
- "Put the gripper together: base, left finger, right finger."
- "Make the J1 to J3 arm subassembly from the committed links."

Not for:

- Making the parts themselves: `generate_cad` or `generate_cad_ir`, one
  committed part each, first.
- A robot description with joints (URDF, SDF, USD): `cadquery.export_urdf_assembly`
  and its siblings on the `robotics` profile.
- Proving parts do not collide: this skill cannot (see Limits).

## Tools and profile

| Step | Tool | Served on |
|---|---|---|
| Check the connection | `health.check` | every profile |
| Find the project and parts | `project.open`, `twin.find_by_property`, `twin.get_node` | every profile |
| Get each part's STEP file | `twin.stage_work_product_file` | `mechanical`, `simulation`, `robotics`, `mechanical_product` |
| Build the assembly | `cadquery.create_assembly` | `mechanical`, `robotics` |
| Inspect the result | `freecad.describe_step_file` | `mechanical` |
| Commit | `twin.commit_geometry` | `mechanical`, `robotics`, `mechanical_product` |
| Record placement choices | `twin.record_decision` | every profile |

Use `?profile=mechanical`. If the cadquery adapter is listed in
`unreachable_adapters`, stop and tell the user before staging anything.

A FreeCAD session route also exists (`freecad.open_session`,
`freecad.import_step`, `freecad.create_assembly`,
`freecad.add_part_to_assembly`, `freecad.add_assembly_joint`,
`freecad.export_model`). It keeps each part's imported colour, honours
rotations through `freecad.transform_object` and can record joints, but `freecad.import_step` and `freecad.create_assembly` are served
only on a connection with **no** profile; `mechanical_product` has
`freecad.add_part_to_assembly` without them, so it cannot build an assembly
on its own. Offer that route only if the user needs joints or per-part
colours and has such a connection.

## Inputs you need before you start

Ask the user. Do not invent placements: a guessed offset produces an
assembly that looks plausible and is wrong.

| Input | Why it matters | Example |
|---|---|---|
| Assembly name | Twin item name of the assembly | `Gripper Assembly` (never the material alone) |
| Which parts, by name or node id | Each must already be a committed `cad_model` in the same project | `Gripper Base`, `Left Finger`, `Right Finger` |
| Each part's position, x/y/z in mm, in the assembly frame | The only placement this tool honours | Left Finger at (-20, 0, 15) |
| Any rotation a part needs | Not honoured by this tool (see below) | "right finger turned 180 deg about Z" |
| Material, if one applies to the whole assembly | First-order mass estimate only | `ABS` |
| Project | The assembly links to the same project as its parts | project name or id |

Each part has its own frame (CadQuery parts are centred on the origin,
FreeCAD parametric parts start at a corner). Positions are offsets of that
frame. Look at each part's bounding box (step 2) before agreeing offsets
with the user.

## Procedure

### 1. Find the parts

1. `health.check`, then `project.open`. Read
   `metaforge://twin/brief/<project_id>` and, for structure,
   `metaforge://twin/hierarchy/<project_id>`.
2. For each part, find its committed `cad_model` node id (`twin.find_by_property`
   or `twin.get_node`). A part that is not committed yet must be built and
   committed first; do not assemble from an uncommitted adapter file you
   cannot trace.
3. If an assembly with this name already exists, this is a new revision of
   it. Keep its node id or item key.

### 2. Stage each part and read it

1. `twin.stage_work_product_file` with each part's `node_id`. Keep the
   returned `file_path`. Use staging even for parts you just made: an adapter
   file path from an earlier call can be overwritten later.
2. `freecad.describe_step_file` on each staged file: confirm the part's
   label, solid count and bounding box. Use the boxes to sanity-check the
   offsets with the user.

### 3. Build the assembly

Call `cadquery.create_assembly`:

- `parts`: one entry per part, `{"name": "Left Finger", "file": <staged
  file_path>, "location": {"x": -20, "y": 0, "z": 15}}`. Part names must be
  unique; they become the component names inside the STEP. Use the part's
  real name, never Part_1.
- `output_path`: a unique relative path such as
  output/gripper_assembly_r2.step
- `material`: only if the user gave one
- `constraints`: leave out unless the user asks for them. Each entry is
  `{"part_a", "part_b", "type"}` with type Point, Axis, Plane or
  PointInPlane, but only part names are passed to the solver, never a face
  or axis, so most constraints cannot say what they mean. Explicit
  `location` values are the reliable placement.

Keep from the result: `assembly_file`, `part_count`, `total_volume`
(`volume_mm3`), `mass_kg` if a material was given.

### 4. Check the result

1. `part_count` equals the number of parts you sent.
2. `freecad.describe_step_file` on `assembly_file`: every part appears under
   its own name with a sensible volume and a bounding box at the position you
   intended. The file also lists the top-level assembly compound whose
   volume is the sum of the parts; do not count it as a part.
3. Overlapping part bounding boxes where the parts should not touch are a
   warning sign. Say "possible interference, not checked", never "no
   interference".

### 5. Commit

`twin.commit_geometry` with:

- `name`: the assembly name
- `file_path`: `assembly_file` as returned
- `project_id`
- `part_node_ids`: the part node ids (or `parts` as
  `[{"node_id", "name", "position_bbox_mm"}]` when you have each part's box
  in the assembly frame). This links the assembly to its parts in the twin.
- `source_tool: "cadquery.create_assembly"`
- `properties`: `volume_mm3` and `mass_kg` from the build result, if present
- `item_key` and `change_reason` when revising an existing assembly under a
  changed name

Read `node_id`, `revision`, `model_url`, `project_linked`. A rejection naming
a part node id means that part is not a `cad_model` in this project.

### 6. Report

Name every part and its position, the assembly node id and revision, the
part links recorded, total volume and mass (stated as an estimate from one
material), and what was not checked: interference and any rotation the user
wanted. Record a placement choice a reviewer could question with
`twin.record_decision` (`title`, `rationale`, `alternatives`).

## Checks before you report

- [ ] Every part was a committed `cad_model`, staged from the twin
- [ ] Every position came from the user
- [ ] Part names in the STEP match the part items, no placeholders
- [ ] `part_count` and the per-part breakdown agree
- [ ] Committed with `part_node_ids` (or `parts`), so the twin links them
- [ ] Interference reported as not checked; rotations reported if dropped

## Failure handling

| Symptom | Likely cause | What to do |
|---|---|---|
| `-32001` naming `cadquery` | Adapter container down | Tell the user; do not loop. |
| Staging fails for a node | Not a committed file-backed work product | Check the node with `twin.get_node`; the part may need committing first. |
| Import error on a part file | Path not from staging, or file replaced | Stage again and pass the new `file_path`. |
| Constraint or solve error | Name-only constraints cannot be solved | Drop constraints; place with `location` values from the user. |
| Commit rejects a part id | Part is in another project or not a `cad_model` | Fix the list; never link a part from another project. |
| Commit held for approval | Writes need a person | Tell the user where it waits; do not retry or reword. |
| `cadquery.create_assembly` not in your list | Wrong profile | Ask for `?profile=mechanical` or `?profile=robotics`. |

## Limits

- **No real interference check.** The result's `interference_check_passed`
  is always true; it is not evidence. Do not report it.
- **Translation only.** `location` accepts rx, ry, rz but the tool ignores
  them. A part that must be rotated has to be modelled in its assembled
  orientation, or the assembly built through the FreeCAD session route. Tell
  the user when a requested rotation was not applied.
- No joints or kinematics in this route; the FreeCAD session route records
  joints but needs a no-profile connection.
- No colours: CadQuery re-imports each part as bare geometry, so any colour
  the part files carried is not kept, and none is added.
- Mass, when given, is the summed volume times one material's density.
