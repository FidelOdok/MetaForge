---
name: generate_cad
description: Generate one named parametric CAD part (plate, L-bracket, open-top enclosure or cylinder) from dimensions the user states, check the solid really matches them, and commit it to the twin as a cad_model. Use when the user asks for a simple plate, bracket, box enclosure, spacer, rod or cylinder with given sizes, wants a quick placeholder part in a project, or needs a first committed geometry before FEA.
domain: mechanical
---

# generate_cad

Turn a part the user has sized into a real STEP solid and a committed,
named `cad_model` in the digital twin, in one short pass. The value is not
the shape itself (these are simple primitives) but a part that is measured,
named, linked to its project and revisable, so later steps (assembly, FEA,
drawings) work on the same item.

## When to use it

- "Make a 120 x 80 x 3 mm aluminium mounting plate."
- "I need a 40 mm diameter, 25 mm long spacer in the arm project."
- "Give me an open-top enclosure 90 x 60 x 35 with 2 mm walls."
- "Put a placeholder L-bracket in so we can run FEA on it."

Not for:

- A part with real features (holes at chosen positions, pockets, fillets,
  revolves, patterns): use `generate_cad_ir` (feature-by-feature FreeCAD
  session) or the scripted path with `cadquery.execute_script`.
- A PCB enclosure sized from a board: `cadquery.generate_enclosure`.
- Several parts placed together: build each part here, then `create_assembly`.
- Changing an existing part's geometry when the user wants it reviewed first:
  run `decide_sketch_needed` before touching CAD.

## Tools and profile

| Step | Tool | Served on |
|---|---|---|
| Check the connection | `health.check` | every profile |
| Find the project and any existing part | `project.open`, `twin.find_by_property`, `twin.get_node` | every profile |
| Generate (default backend) | `cadquery.create_parametric` | `mechanical` |
| Generate (FreeCAD backend) | `freecad.create_parametric` | `mechanical`, `robotics` |
| Inspect the file | `cadquery.get_properties`, `freecad.describe_step_file` | `mechanical` |
| Commit | `twin.commit_geometry` | `mechanical`, `robotics`, `mechanical_product` |
| Record a choice | `twin.record_decision` | every profile |

Connect with `?profile=mechanical`. If the generate tools are missing, read
`health.check` (its `profile` block, field `active`) before concluding anything is broken and
tell the user which profile to use. If `status` is `degraded` and
`unreachable_adapters` names `cadquery` or `freecad`, say so before you start.

## Inputs you need before you start

Ask the user for anything missing. Do not fill these with defaults: both
tools silently fall back to built-in defaults (a 10 mm cylinder radius, a
3 mm bracket hole) for any dimension you leave out.

| Input | Why it matters | Example |
|---|---|---|
| Part name | Becomes the twin item name; reusing it later revises the same item | `Motor Mount Plate` |
| Shape | Picks the builder and its parameter keys | plate, bracket, enclosure, cylinder |
| Every dimension for that shape, in mm | Missing keys become silent defaults | `length 120, width 80, thickness 3` |
| Material | Drives `mass_kg` on the committed node and later FEA | `aluminum_6061`, `steel`, `ABS` |
| Project | The node must link to a project to show in the viewer | the project name or id |

Never pick a part name like `Part_1`, `Body`, `Box` or the material alone.
Use what the part is.

### Parameter keys per shape (exact names, millimetres)

| Shape | Keys | What you get |
|---|---|---|
| `plate` | `length`, `width`, `thickness` | A solid rectangular plate |
| `bracket` | `length`, `width`, `thickness`, `hole_radius` | An L-bracket: base plate, an upright leg whose height is fixed at half of `length`, and one through-hole in the base. Hole position and leg height cannot be set. |
| `enclosure` | `length`, `width`, `height`, `wall_thickness` | A hollow box with the top face open |
| `cylinder` | `radius`, `height` | A solid cylinder. `freecad.create_parametric` also accepts `diameter`; `cadquery.create_parametric` does not. |

`cadquery.create_parametric` also builds `box` (`length`, `width`, `height`),
`sphere` (`radius`) and `cone` (`radius1`, `radius2`, `height`). Use them only
when the part genuinely is that primitive.

If the user's part does not fit one of these exactly (the hole must be
elsewhere, the leg must be taller), say so and switch to `generate_cad_ir`
rather than approximating.

## Procedure

### 1. Connect and find context

1. `health.check` once. Note the active profile it reports and any unreachable adapter.
2. `project.open` with the user's words as `query`. Several matches come back
   as an error listing them: ask which one. Keep the `project_id`.
3. Read `metaforge://twin/brief/<project_id>`. If a part with this name (or
   clearly the same part under another name) is already committed, this is a
   revision, not a new part. Note its node id and item key.

### 2. Generate

Call `cadquery.create_parametric` (or `freecad.create_parametric` if the
user asked for FreeCAD or cadquery is unreachable):

- `shape_type`: from the table above
- `parameters`: only the keys listed for that shape, every one given
- `material`: the user's material name
- `output_path`: a unique relative path such as
  output/motor_mount_plate_r2.step. Never reuse a generic name like
  output/plate.step: a later call writing the same path silently replaces
  the file an earlier step still points at.

Keep from the result: `cad_file`, `volume_mm3`, `surface_area_mm2`,
`bounding_box`, `parameters_used` and `mass_kg` (present only when a known
material was given).

### 3. Check the solid is what was asked for

1. Compare `parameters_used` key by key with what the user stated.
   `cadquery.create_parametric` ignores unknown keys (for example `diameter`)
   and quietly uses its default instead, so a mismatch here is the commonest
   silent error. `freecad.create_parametric` rejects unknown keys instead.
2. Compare the `bounding_box` extents with the expected outer size. The two
   backends place parts differently: CadQuery centres the solid on the
   origin, FreeCAD starts it at the origin corner. Note which, because a
   later assembly or FEA face selection depends on it.
3. Check `volume_mm3` is plausible (a plate is about length x width x
   thickness). Zero or a wildly different volume means stop and report.
4. No `mass_kg` in the result means the material was not recognised. Tell
   the user rather than computing a mass from a guessed density.

### 4. Commit it

Call `twin.commit_geometry` by reference:

- `name`: the part name
- `file_path`: the `cad_file` exactly as returned (no base64)
- `project_id`
- `source_tool`: `cadquery.create_parametric` or `freecad.create_parametric`
  (the default would wrongly claim `freecad.export_model`)
- `parameters`: the dimensions you passed, so a later change is recognised as
  an edit of the same part
- `properties`: `volume_mm3`, `surface_area_mm2`, `mass_kg` and
  `bounding_box` from the generate result. Without them, a requirement such
  as "mass <= 0.5 kg" can never genuinely pass or fail.
- For a revision of an existing item under a new name, add `item_key` and a
  `change_reason`. Same name in the same project already means same item.

Read the reply: `node_id`, `model_url`, `project_linked`, `item_key`,
`revision`. `already_committed: true` means identical geometry under this
name already existed and nothing new was created: do not commit again.

### 5. Record and report

- If you chose something a reviewer could question (backend, a shape that
  only approximates the user's part), call `twin.record_decision` with
  `title`, `rationale` and the `alternatives` you actually considered.
- Report the node id, revision, outer dimensions from `bounding_box`, volume,
  mass (or "material not recognised, no mass"), and the viewer link.

## Checks before you report

- [ ] Every dimension came from the user, and `parameters_used` matches it
- [ ] The part has a real name, not a placeholder
- [ ] `bounding_box` agrees with the requested outer size
- [ ] Committed with `file_path`, `source_tool` and `properties`
- [ ] `project_linked` is true, or you told the user it is not
- [ ] You did not claim colours: these tools write no colour into the STEP,
      so the viewer shows the part uncoloured

## Failure handling

| Symptom | Likely cause | What to do |
|---|---|---|
| `-32001` naming `cadquery` or `freecad` | Adapter container down | Tell the user which one. Try the other backend once if it is up; do not loop. |
| "Unsupported shape type" | Shape outside the backend's list | Use the table above, or move to `generate_cad_ir`. |
| "unknown parameter(s)" from FreeCAD | Wrong key name | Use the listed keys; FreeCAD names the accepted ones. |
| Dimensions in the result differ from the request | Unknown key ignored by CadQuery | Fix the key and generate again with a new `output_path`. |
| "could not read file_path" on commit | Path edited, or the file was overwritten | Pass `cad_file` unchanged; regenerate if needed. |
| Commit held for approval | Writes need a person on this connection | Tell the user where it waits. Do not retry or reword the call. |
| Generate or commit tool not in your list | Wrong profile | Check `health.check` and ask for `?profile=mechanical`. |

## Limits

- One solid per call. No holes at chosen positions, fillets, chamfers or
  patterns; the bracket's hole position and leg height are fixed.
- The STEP written by these stateless tools carries no part name and no
  colour. The twin item takes the `name` you commit with. A per-part STEP
  label or an authored colour needs the FreeCAD session path
  (`freecad.export_model` with `material` or `color`), which is in the
  `mechanical_product` profile, not `mechanical`.
- Mass is volume times one tabulated density. It is a first estimate, not a
  weighed value.
