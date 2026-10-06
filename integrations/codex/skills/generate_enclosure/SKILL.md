---
name: generate_enclosure
description: Generate an open-top PCB enclosure (shelled box with connector cutouts and mounting posts) from the board's real dimensions with CadQuery, check it fits, and commit it to the twin as a named CAD model. Use when the user asks for a case, housing or enclosure around a PCB ("make an enclosure for the 60 x 40 mm controller board"), when a hardware flow needs a mechanical housing for an electronics deliverable, or when board size and connector positions are known and a first-fit box is wanted.
domain: mechanical
---

# generate_enclosure

Build a first-fit enclosure around a printed circuit board: a shelled box
sized from the board's outline and tallest component, with rectangular
connector cutouts and mounting posts with holes, then commit it to the twin so
the electronics and mechanical sides share one reviewed housing. It is a
cross-domain step: the board numbers come from the electronics side, the
housing belongs to mechanical.

## When to use it

- "Make an enclosure for the 60 x 40 mm controller board, 2 mm walls."
- "Add a USB-C cutout on the front and four M3 mounting posts."
- A `hardware_v1` design phase needs a housing `cad_model` for a board whose
  size is already recorded.

Not for:

- A lid, snap fits, screw bosses for a lid, vents or organic shapes: the tool
  makes one open-top box only. Script those with `generate_cad_script`, or on
  a full-set connection add bosses with `freecad.thread_insert`.
- A plain hollow box with no board inside (no posts, no cutouts):
  `freecad.generate_enclosure` (full set only) takes outer `length`, `width`,
  `height` directly in a FreeCAD session.
- Reading board size out of KiCad files: the KiCad tools here do not report a
  board outline. Get the numbers from the user or the recorded PCB data.

## Tools and profile

| Step | Tool | Profile that serves it |
|---|---|---|
| Check the connection | `health.check` | every profile |
| Find the project and the board | `project.open`, `twin.find_by_property`, `twin.get_node` | every profile |
| Generate the enclosure | `cadquery.generate_enclosure` | `mechanical` |
| Re-measure the file | `cadquery.get_properties` | `mechanical`, `simulation` |
| Commit to the twin | `twin.commit_geometry` | `mechanical`, `robotics` |
| Record a design choice | `twin.record_decision` | every profile |

All required tools are on the **`mechanical`** profile. If
`cadquery.generate_enclosure` is not in your list, check `health.check`
`profile` and ask the user to connect with `?profile=mechanical`.

## Inputs you need before you start

Ask the user for anything missing. Do not default these, even where the tool
has a default.

| Input | Why it matters | Example |
|---|---|---|
| Enclosure name | Twin item name. Specific to what it is, never just the material | `Controller Base Housing` |
| `pcb_length`, `pcb_width` (mm) | Set the inner footprint | 60, 40 |
| `pcb_thickness` (mm) | Part of the inner height | 1.6 |
| `component_max_height` (mm) | Tallest part above the board; too low and the board does not fit | 12 (electrolytic cap) |
| `wall_thickness` (mm) | Strength and printability; also sets floor thickness | 2.0 |
| Each connector cutout | `side` (front, back, left, right), `width`, `height`, and `x`, `z` offsets from the centre of that wall | USB-C: front, 9.5 x 3.5, x = 0, z = -2 |
| Each mounting hole | `x`, `y` from the PCB origin (a board corner), `diameter` | (3.5, 3.5, 3.2) |
| Material | Recorded on the part and used for mass | `ABS`, `PETG` |
| Project | The commit links the housing to it | project name or id |

Confirm which board corner is the origin for the hole coordinates and which
wall is "front" (the tool's front is the +Y wall). Connector positions are
where most enclosure errors come from.

## Procedure

### 1. Check the connection and gather the board data

1. `health.check`: `cadquery` must not be in `unreachable_adapters`.
2. `project.open` with the project name. Read
   `metaforge://twin/brief/<project_id>` for a recorded PCB, connector or
   mechanical envelope requirement (maximum outer size, IP rating notes).
3. If a PCB work product exists, `twin.get_node` on it and use the recorded
   dimensions, telling the user which node they came from. Otherwise ask.

### 2. Generate

Call `cadquery.generate_enclosure` with:

- `pcb_length`, `pcb_width`, `pcb_thickness`, `component_max_height`,
  `wall_thickness`, `material`
- `connector_cutouts`: a list of `{"side": "front", "width": 9.5,
  "height": 3.5, "x": 0, "z": -2}`. `side` must be exactly `front`, `back`,
  `left` or `right`; any other value is **silently skipped**, not an error.
- `mounting_holes`: a list of `{"x": 3.5, "y": 3.5, "diameter": 3.2}` in
  board coordinates
- `output_path`: a distinct STEP path, e.g. output/controller_base_housing.step.
  Without it every call writes the same shared enclosure.step.

### 3. Read and check the result

The reply carries `cad_file`, `internal_volume`, `external_dimensions`
(`length`, `width`, `height`), `mounting_info` (`hole_count`,
`cutout_count`), `material`, and the measured `volume_mm3`,
`surface_area_mm2`, `bounding_box` and `mass_kg`.

1. The `cutout_count` inside `mounting_info` counts the cutouts you sent, not the ones
   that were cut: a cutout with an unrecognised `side` is skipped and still
   counted. Check every `side` before the call, and open `model_url` after
   committing before telling the user every connector opening is there.
2. The tool adds a fixed 1 mm clearance on each side of the board and above
   the tallest component. Inner size is board + 2 mm each way; outer size
   adds two walls. Check `external_dimensions` against any envelope
   requirement and report the margin.
3. Mounting posts are wall_thickness + 1 mm tall with an outer diameter of
   hole diameter + 2 mm. Tell the user these are fixed by the tool, not chosen.
4. `volume_mm3` must be positive.

### 4. Commit by reference

Call `twin.commit_geometry` with:

- `name`: the enclosure name
- `file_path`: the returned `cad_file`, unchanged
- `project_id`
- `source_tool`: `"cadquery.generate_enclosure"`
- `parameters`: every input you passed (board size, wall, clearance-relevant
  heights, cutouts, holes, material), so a later revision can be compared
- `properties`: `volume_mm3`, `surface_area_mm2`, `bounding_box`, `mass_kg`
  from the reply, so mass and envelope constraints can be evaluated
- `item_key` and `change_reason` when revising an existing housing under a
  changed name

Read `node_id`, `model_url` and `project_linked`.

### 5. Record and report

- Record non-obvious choices with `twin.record_decision` (wall thickness
  chosen for a print process, a cutout moved to clear a component), with the
  alternatives considered.
- Report the outer dimensions, the inner clearance, holes and cutouts as
  requested, `node_id`, `model_url`, and what the tool cannot do (no lid).

## Checks before you report

- [ ] Board size, component height and every cutout and hole came from the user or a recorded PCB node
- [ ] Every cutout `side` is one of front, back, left, right
- [ ] Hole coordinates use the board-corner origin the user confirmed
- [ ] Outer dimensions checked against any envelope requirement
- [ ] Committed by `file_path` with `properties`, under a specific name
- [ ] Told the user the enclosure is open-top with no lid

## Failure handling

| Symptom | Likely cause | What to do |
|---|---|---|
| `-32001` naming `cadquery` | Adapter container down | Tell the user; do not retry in a loop. |
| "pcb_length must be positive" | Missing or zero board size | Ask for the real board size. |
| A connector opening missing in the viewer | `side` misspelt, or cutout positioned off the wall | Fix `side` or the offsets and regenerate. |
| Cutout through a mounting post or the floor | `z` offset measured from the wrong reference | Offsets are from the wall's centre; recompute and regenerate. |
| Holes in the wrong place | Board origin assumed differently | Confirm the origin corner; regenerate. |
| Script or boolean failure from the adapter | Cutout larger than the wall, or overlapping features | Tell the user which feature; ask for corrected numbers. |
| `twin.commit_geometry` cannot read the file | Path changed or workspace recreated | Regenerate, pass the new `cad_file` exactly. |
| Commit `approval_required` / held | Writes need a person | Report where it waits; do not retry. |
| Tool not in your list | Wrong profile | `health.check` `profile`; ask for `?profile=mechanical`. |

## Limits

- One open-top shelled box. No lid, snap fits, lid screw bosses, ribs,
  vents, draft or fillets.
- Rectangular cutouts only; round connectors need a scripted cut.
- Clearance (1 mm) and post size are fixed in the tool.
- The STEP carries no part name or colour; the twin name comes from
  `twin.commit_geometry`. The viewer will show it uncoloured, which is
  correct: do not describe or apply a colour the user did not author.
- It fits the board; it does not check that the board's connectors line up
  with real panel positions or that the housing survives a drop or a load.
