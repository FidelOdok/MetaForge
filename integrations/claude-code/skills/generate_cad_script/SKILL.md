---
name: generate_cad_script
description: Write a CadQuery (or FreeCAD) script for a described part, run it in the MetaForge sandbox, check the geometry against the user's dimensions, and commit it to the twin as a named CAD model. Use when the user asks for a part whose shape is more than a single primitive ("model a bracket with a slot and two counterbored holes", "write a CadQuery script for this flange"), hands you a script to run and record, or a design phase needs a cad_model deliverable that no dedicated generator covers.
domain: mechanical
---

# generate_cad_script

Turn a described part into real, measured geometry by writing the script
yourself and running it in MetaForge's sandboxed CAD kernel, then leave a
named, versioned CAD model in the twin that a reviewer can open in the viewer.
The script is the design: every dimension in it must trace back to the user.

## When to use it

- "Model a 3 mm aluminium L-bracket, 40 x 30 mm legs, two M4 clearance holes."
- "Here is my CadQuery script for the sensor mount; run it and save it to the project."
- "Make the spacer from the PRD: 12 mm OD, 6.4 mm bore, 8 mm long."
- A design phase requires a `cad_model` deliverable and the part has features
  (slots, pockets, fillets, holes) a plain box or cylinder cannot express.

Not for:

- A plain box, cylinder or sphere: `cadquery.create_parametric` or
  `freecad.create_parametric` is one call with no script.
- A PCB enclosure from board dimensions: use `generate_enclosure`.
- A bolt-circle mounting plate or a gusset rib: use `generate_parametric_feature`.
- Joining several committed parts into an assembly: use `create_assembly`
  (`cadquery.create_assembly`), not one script that fuses everything.
- Editing a part that is already committed: the CadQuery sandbox cannot read
  files, so stage it with `twin.stage_work_product_file` and load it into a
  FreeCAD session with `freecad.import_step` (FreeCAD path below), or rewrite
  the script and commit it under the same name as a new revision.

## Tools and profile

| Step | Tool | Profile that serves it |
|---|---|---|
| Check the connection | `health.check` | every profile |
| Find the project and existing parts | `project.open`, `twin.find_by_property`, `twin.get_node` | every profile |
| Run a CadQuery script | `cadquery.execute_script` | `mechanical` |
| Re-measure a produced file | `cadquery.get_properties` | `mechanical`, `simulation` |
| Per-part breakdown of a STEP | `freecad.describe_step_file` | `mechanical` |
| Commit to the twin | `twin.commit_geometry` | `mechanical`, `robotics` |
| Record a design choice | `twin.record_decision` | every profile |
| FreeCAD dialect | `freecad.open_session`, `freecad.execute_code`, `freecad.measure`, `freecad.export_model`, `freecad.close_session` | full set only (connect with no profile) |

The CadQuery path is the default and runs entirely on the `mechanical`
profile. The FreeCAD session tools are not on any named profile: if the user
needs the FreeCAD dialect and `freecad.open_session` is not in your tool list,
tell them to reconnect without `?profile=` rather than concluding FreeCAD is
missing.

## Inputs you need before you start

Ask the user for anything missing. Do not fill these in with defaults or
"typical" values.

| Input | Why it matters | Example |
|---|---|---|
| Part name | Becomes the twin item name (and, on the FreeCAD path, the STEP PRODUCT name). Never `Part_1`, `Body`, `Box`, `Result` | `Sensor Mount Bracket` |
| Every dimension, in mm | The script hard-codes them; a guessed one is an invented design | "legs 40 and 30 mm, 3 mm thick" |
| Hole sizes and positions | Clearance vs tapped vs press fit changes the number | "two M4 clearance, 4.5 mm, 10 mm from each end" |
| Datum and orientation | Which face sits on XY, where the origin is; FEA and assembly later rely on it | "mounting face on XY, origin at the corner" |
| Material | Recorded with the part; on the FreeCAD path it is written into the STEP so the viewer colours it | `aluminum_6061`, `PETG` |
| Project | The commit links the part to it | the project name or id |
| Dialect, if the user cares | CadQuery is the default; FreeCAD when they want a session they can keep editing | `cadquery` |

If the project already has a part with this name, you are making a new
revision of it, not a new part. Confirm that with the user.

## Procedure

### 1. Check the connection and the project

1. Call `health.check`. If `cadquery` (or `freecad`, for that dialect) is in
   `unreachable_adapters`, stop and tell the user; nothing below will run.
2. Call `project.open` with the user's project name as `query`. Several
   matches come back as an error listing them: ask which one.
3. Read `metaforge://twin/brief/<project_id>`. Look for an existing part with
   the same name, and for requirements that bound this part (mass, envelope).

### 2. Write the script (CadQuery)

- Put every user-supplied value in a named variable at the top, in mm, with a
  comment saying where it came from. No unexplained literals in the geometry.
- Assign the final solid to a variable named `result` (a CadQuery Workplane).
- Only `cq`/`cadquery`, `math` and cadquery's top-level names (`Workplane`,
  `Vector`, ...) are available. `import cadquery as cq` and `import math`
  lines are stripped and are harmless; nothing else can be imported.
- These words are blocked anywhere in the script, including comments and
  strings, as whole words: `open`, `os`, `sys`, `subprocess`, `eval`, `exec`,
  `compile`, `__import__`. A comment such as "open top" fails the call; write
  "open-top" or "unlidded".
- At most 200 lines. No file reads or writes; the tool exports for you.

### 3. Run it

Call `cadquery.execute_script` with:

- `script`: the script text
- `output_path`: a distinct STEP path for this part, e.g.
  output/sensor_mount_bracket.step. If you leave it out, every call writes the
  same shared script_result.step and the next run overwrites yours.
- `timeout`: only if the user's geometry is known to be heavy

Read the result: `cad_file`, `script_text` (what actually ran, after import
stripping), `volume_mm3`, `surface_area_mm2`, `bounding_box`, and
`step_base64`.

### 4. Check the geometry before committing

1. `volume_mm3` must be greater than zero and `bounding_box` must not be null.
   A null box means the result had no solid (an unclosed sketch, a cut that
   removed everything).
2. Compare `bounding_box` extents against the dimensions the user gave. A
   40 x 30 x 3 bracket whose box is 40 x 30 x 30 has a sign or axis error.
3. Sanity-check the volume against a hand estimate (outer volume minus holes).
   A volume equal to the outer box means the holes did not cut.
4. If you need a second opinion, `cadquery.get_properties` with
   `input_file` = the `cad_file` re-measures the written STEP.

If any check fails, fix the script and run it again. Do not commit geometry
you know is wrong.

### 5. Commit by reference

Call `twin.commit_geometry` with:

- `name`: the part name from the inputs
- `file_path`: the `cad_file` the tool returned, unchanged (no base64)
- `project_id`
- `source_tool`: `"cadquery.execute_script"` (otherwise the node claims
  FreeCAD authored it)
- `script_source`: the `script_text` from step 3, so the script is versioned
  as the part's source of truth
- `parameters`: the named values that drove the script, plus `material`
- `properties`: `volume_mm3`, `surface_area_mm2` and `bounding_box` from step 3.
  Without them, any mass or envelope constraint on this part can never pass
  or fail; it is flagged `measured_properties_missing`.
- `item_key` and `change_reason` when this revises an existing part whose
  name changed

`cadquery.execute_script`'s own description says `step_base64` is the only
way to commit its output. That text is out of date: `twin.commit_geometry`
reads `file_path` for stateless tools. Use `file_path`; fall back to
`step_base64` only if the commit reports it cannot read the file.

Read the reply: `node_id`, `model_url`, `project_linked`. `project_linked:
false` means the part exists but is not on the project; say so.

### 6. FreeCAD dialect (when the user wants it)

1. `freecad.open_session` with `name` = the part name. Keep the `session_id`.
2. `freecad.execute_code` with `session_id` and `code`. The namespace has
   `FreeCAD` (also `App`), `Part`, `Import`, `math`, `doc` and the bare
   `Vector`, `Rotation`, `Placement`, `Matrix`. The same blocked words apply.
   Build the shape, then put it on a named document object and assign that
   object (not a bare shape) to `result`:
   `obj = doc.addObject("Part::Feature", "SensorMountBracket")`,
   `obj.Label = "Sensor Mount Bracket"`, `obj.Shape = shape`, `result = obj`.
   A bare `Part.Shape` in `result` is not registered and returns no `obj_id`.
   Never assign the same Shape to two objects' `.Shape` (it crashes the
   adapter); call `shape.copy()` for the second. Do not export inside the
   script (no `exportStep`, no `Part.export`); step 5 below does it properly.
3. Read `obj_id` from the reply. Call `freecad.measure` with `session_id` and
   `obj_id`, and run the checks from step 4 on its volume and bounding box.
4. Call `freecad.export_model` with `session_id`, `obj_id`, and `material`
   only if the user named one (an unknown material stays uncoloured; do not
   substitute a colour). It preserves the Label as the STEP PRODUCT name.
5. Call `twin.commit_geometry` with the **same** `session_id` and `obj_id`,
   plus `name`, `project_id`, `script_source` (the code) and `parameters`.
   Both ids are required together on every call, including a retry.
6. Call `freecad.close_session`, also when an earlier step failed.

### 7. Record and report

- When you chose between real alternatives (a fillet radius the user left to
  you after you asked, a datum choice), record it with `twin.record_decision`
  including the alternatives.
- Report: part name, `node_id`, `model_url`, measured volume and bounding box
  against the requested dimensions, the dialect used, and anything not done.

## Checks before you report

- [ ] Every dimension in the script came from the user or a cited document
- [ ] The part has a meaningful name, not a placeholder
- [ ] Volume is positive and the bounding box matches the requested extents
- [ ] Committed by reference (`file_path`, or `session_id` + `obj_id`), not pasted base64
- [ ] `properties` passed, so constraints on this part can be evaluated
- [ ] No colour was invented; colour only from a material the user named
- [ ] FreeCAD session closed

## Failure handling

| Symptom | Likely cause | What to do |
|---|---|---|
| `-32001` naming `cadquery` or `freecad` | Adapter container down | Tell the user which adapter; do not retry in a loop. |
| "Script contains blocked name" | A blocked word, often in a comment ("open") or a variable named `os` | Rename or reword it and run again. |
| "must assign its output to a variable named 'result'" | No `result` variable | Assign the final Workplane (or document object) to `result`. |
| "Script exceeds maximum of 200 lines" | Script too long | Factor repeated features into loops. |
| Script timeout | Heavy booleans or fillets | Simplify, or pass a longer `timeout` once and tell the user. |
| `freecad.execute_code` returns no `obj_id` | `result` was a bare shape or a dict | Put the shape on a `Part::Feature` and assign that object. |
| Volume 0 or bounding box null | Nothing solid was produced | Fix the sketch or the cut and re-run. |
| `twin.commit_geometry` cannot find the file | Path edited, or adapter workspace recreated | Re-run the script, pass the new `cad_file` exactly as returned. |
| Commit `approval_required` / held | Writes need a person on this connection | Tell the user where it waits; do not retry or reword it. |
| `cadquery.execute_script` not in your tool list | Connected on a profile without it | Check `health.check` `profile`; ask for `?profile=mechanical`. |
| `freecad.open_session` not in your tool list | Session tools are full-set only | Ask the user to reconnect without a profile, or use CadQuery. |

## Limits

- Single solid parts. For several parts, script each one, commit each, and
  assemble them with `create_assembly`.
- The CadQuery path writes no part name or colour into the STEP; the twin
  name comes from `twin.commit_geometry`'s `name`. When the STEP itself must
  carry the name or the material colour, use the FreeCAD path.
- `cadquery.execute_script` exports whatever format `output_path` ends in,
  but only STEP can be committed. Keep the .step extension.
- The sandbox has no file access, so a script cannot load an existing part.
- MetaForge checks that the script runs and measures the result. It does not
  check manufacturability, tolerances or strength; those are other skills.
