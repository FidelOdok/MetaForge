---
description: Build one of MetaForge's named parametric features (bolt_pattern, a plate with a circular bolt-hole pattern, or rib, a triangular gusset) from typed parameters in a FreeCAD session, check it, and commit it with its parameters so a later change becomes a linked revision. Use when the user asks for a bolt circle, a mounting plate with N holes on a pitch circle, a motor-face or flange hole pattern, a gusset or stiffening rib, or asks to change one parameter of such a feature they already made.
---

# generate_parametric_feature

Generate a standard, reusable feature from a few typed numbers instead of a
hand-written model, and commit it with those numbers attached so the feature
can be edited later by changing one value. Two features exist today:
`bolt_pattern` and `rib`. Each is a short, fixed sequence of FreeCAD
PartDesign steps that you drive yourself through the MCP tools.

## When to use it

- "A 60 x 60 x 5 mm plate with 4 x M3 clearance holes on a 40 mm bolt circle."
- "Motor mount face: 6 holes, 3.4 mm, on a 25 mm pitch radius."
- "A triangular gusset rib, 30 mm base, 20 mm high, 3 mm thick."
- "Change the bolt circle radius on the mounting plate to 22 mm."

Not for:

- Holes added to an existing part: this makes a standalone part. Model the
  holes into that part (`generate_cad_script`), or on a full-set connection
  use `freecad.fastener_hole` on its body.
- Bearing seats, motor mounts, clevises, gear stages or cable channels: not in
  the library yet. A spur gear has its own tool (`freecad.generate_gear`);
  for the rest use `generate_cad_script` and ask the user for the real
  vendor dimensions.
- A rectangular or linear hole grid: `generate_cad_script`, or
  `freecad.linear_pattern` in a session.

## Tools and profile

| Step | Tool | Profile that serves it |
|---|---|---|
| Check the connection | `health.check` | every profile |
| Open and close a session | `freecad.open_session`, `freecad.close_session` | `mechanical_product`, full set |
| Body, sketches, pad, pocket | `freecad.create_body`, `freecad.create_sketch`, `freecad.pad_sketch`, `freecad.pocket_sketch` | `mechanical_product`, full set |
| Pattern | `freecad.polar_pattern` | full set only |
| Measure | `freecad.measure` | `mechanical_product`, full set |
| Export | `freecad.export_model` | `mechanical_product`, full set |
| Commit | `twin.commit_geometry` | `mechanical`, `mechanical_product`, `robotics`, full set |
| Fallback: scripted feature | `cadquery.execute_script` | `mechanical` |
| Revision history | `twin.item_history` | full set only |

The basic FreeCAD session tools (open, body, sketch, pad, pocket, measure,
export) are served on the `mechanical_product` profile. The pattern tools are
served only when the client connects with **no profile** (the full tool set),
so a bolt circle or rib array needs that, or the CadQuery fallback in step 6 on
the `mechanical` profile. If `freecad.open_session` is not in your list, ask
the user to reconnect with `?profile=mechanical_product` (or no profile).

## Inputs you need before you start

Ask the user for every value. A bolt pattern with a guessed radius does not
fit the part it is meant to bolt to.

| Input | Why it matters | Example |
|---|---|---|
| Part name | Twin item name and STEP PRODUCT name; also how a later edit finds this part | `Motor Mount Plate` |
| Project | Required for a later edit to be linked as a revision | project name or id |
| `bolt_pattern`: `plate_length_mm`, `plate_width_mm`, `plate_thickness_mm` | Plate size | 60, 60, 5 |
| `bolt_pattern`: `hole_diameter_mm`, `hole_count`, `pattern_radius_mm` | The pattern itself; from the mating part's datasheet | 3.4, 4, 20 |
| `rib`: `length_mm`, `height_mm`, `thickness_mm` | Base, rise and extrusion of the triangle | 30, 20, 3 |
| Material | Written into the STEP so the viewer colours it; recorded with the part | `aluminum_6061` |

Before building, check the bolt pattern is physically possible and, if not,
tell the user which rule fails and ask for new numbers (do not adjust them):

- `hole_count` is at least 2
- `pattern_radius_mm + hole_diameter_mm / 2` is less than half the shorter
  plate side
- `2 * pi * pattern_radius_mm / hole_count` is greater than `hole_diameter_mm`
  (otherwise neighbouring holes overlap)

## Procedure

### 1. Prepare

1. `health.check`: `freecad` must be reachable.
2. `project.open` with the project name. In
   `metaforge://twin/brief/<project_id>`, check whether a part with this name
   exists. If it does, this run is an edit and will become its next revision.

### 2. Build a bolt_pattern

Keep every returned `obj_id`.

1. `freecad.open_session` with `name` = the part name. Keep `session_id`.
2. `freecad.create_body` with `session_id` and `name` = the part name (this
   Label becomes the STEP PRODUCT name; do not leave a generic name).
3. `freecad.create_sketch` with `body_id`, `plane: "XY"` and one rectangle
   centred on the origin:
   `{"type": "rectangle", "x": -L/2, "y": -W/2, "width": L, "height": W}`.
4. `freecad.pad_sketch` with `body_id`, `sketch_id` and `length` = plate thickness.
5. `freecad.create_sketch` on `plane: "XY"` with one circle on the pitch
   circle: `{"type": "circle", "cx": R, "cy": 0, "r": d/2}`.
6. `freecad.pocket_sketch` with `body_id`, that `sketch_id`, `depth` = plate
   thickness and **`reversed: true`**. With `reversed: false` the pocket cuts
   away from the plate into empty space and removes nothing.
7. `freecad.polar_pattern` with `body_id`, `feature_id` = the pocket's
   `obj_id`, `count` = hole count, `angle: 360`, `axis: "Z"`.

### 3. Or build a rib

1. `freecad.open_session`, then `freecad.create_body` with the part name.
2. `freecad.create_sketch` with `plane: "XZ"` and three lines forming a right
   triangle: `{"type": "line", "x1": 0, "y1": 0, "x2": L, "y2": 0}`,
   `{"type": "line", "x1": L, "y1": 0, "x2": 0, "y2": H}`,
   `{"type": "line", "x1": 0, "y1": H, "x2": 0, "y2": 0}`.
3. `freecad.pad_sketch` with `length` = thickness and `midplane: true`.

### 4. Measure and check

Call `freecad.measure` with `session_id` and the last feature's `obj_id`.

- `bolt_pattern`: volume should be `L*W*T - N*pi*(d/2)^2*T`. A volume equal
  to `L*W*T` means the pocket cut nothing (check `reversed`). A volume short
  by only one hole means the pattern did not apply.
- `rib`: volume should be `0.5*L*H*T`.
- The bounding box should be L x W x T (plate) or L x T x H (rib).

If the numbers disagree by more than rounding, fix the step and re-measure.
Do not commit a feature whose volume you cannot explain.

### 5. Export and commit

1. `freecad.export_model` with `session_id`, `obj_id` = the **body's**
   `obj_id` (not the pattern or pad; the body carries the part name), and
   `material` if the user named one.
2. `twin.commit_geometry` with the **same** `session_id` and `obj_id`, plus:
   - `name`: the part name
   - `project_id`
   - `parameters`: every typed value plus `feature_type`, e.g.
     `{"feature_type": "bolt_pattern", "plate_length_mm": 60, ...}`. This is
     what lets a later edit be compared parameter by parameter.
   - `properties`: the volume, area and bounding box from `freecad.measure`
3. `freecad.close_session`, also after a failure.

Read `node_id`, `model_url`, `project_linked`.

### 6. Fallback without session tools

On the `mechanical` profile, write the same feature as a CadQuery script with
the user's values (a rectangle `.extrude()`, then holes placed with
`.polarArray(R, 0, 360, N)` and `.hole(d)`; or a triangular `.polyline()` on
XZ, closed and extruded symmetrically), run it with `cadquery.execute_script`
and a distinct `output_path`, apply the same volume checks, and commit with
`twin.commit_geometry` using `file_path` = the returned `cad_file`,
`source_tool: "cadquery.execute_script"`, `script_source`, and the same
`parameters` including `feature_type`. Tell the user this path writes no part
name into the STEP.

### 7. Editing a feature

Run the same procedure with the **same** `name` and `project_id` and the
changed value. The commit links the new node to the one it replaces as its
next revision. Without `project_id` no link is made and you get an unrelated
duplicate. If `twin.item_history` is in your list, use it to show the user
the revisions; otherwise report the new and previous `node_id`.

## Checks before you report

- [ ] Every parameter came from the user; impossible patterns were refused, not adjusted
- [ ] Body named with the part name, and the body was exported
- [ ] Measured volume matches the closed-form volume
- [ ] Committed by `session_id` + `obj_id`, with `parameters` including `feature_type`
- [ ] `project_id` passed, so edits link as revisions
- [ ] Session closed

## Failure handling

| Symptom | Likely cause | What to do |
|---|---|---|
| `-32001` naming `freecad` | Adapter container down | Tell the user; do not loop. |
| `freecad.open_session` not in your tool list | Connected on a profile without the session tools | Ask for `?profile=mechanical_product` or no profile, or use step 6. |
| Volume unchanged after the pocket | `reversed` was false | Redo the pocket with `reversed: true`. |
| Only one hole cut | `polar_pattern` given the sketch, not the pocket | Pass the pocket's `obj_id` as `feature_id`. |
| PartDesign error on pad or pocket | Sketch not closed (rib lines not meeting) | Fix the line endpoints so they form a closed loop. |
| STEP PRODUCT name generic | Exported the tip feature, or body unnamed | Export the body; name it at `freecad.create_body`. |
| Commit does not match the export | `session_id` omitted on a retry | Pass both ids on every call. |
| Commit `approval_required` / held | Writes need a person | Report where it waits; do not retry. |
| Edit created a new unrelated part | Different `name` or no `project_id` | Re-commit with the original name and project, or pass `item_key` and `change_reason`. |

## Limits

- Two features: `bolt_pattern` and `rib`. Holes are plain through-holes, no
  counterbore or thread.
- The bolt pattern is centred on the plate and starts on the +X axis.
- Each run makes a standalone part; it never modifies another part's geometry.
- Linking an edit to its previous revision needs the same name and a project.
