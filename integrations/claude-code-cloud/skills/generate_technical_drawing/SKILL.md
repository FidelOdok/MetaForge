---
name: generate_technical_drawing
description: Record a drawing package for a committed CAD part (toleranced dimensions, GD&T callouts, surface finishes and inspection requirements) as structured data linked to that part, after checking the nominal values against the real geometry. Use when the user asks for a drawing, a drawing spec, tolerances, GD&T or surface finish callouts for a part, or an inspection sheet, or when a manufacturing phase needs a technical_drawing deliverable. It does not render a 2D drawing sheet.
domain: mechanical
---

# generate_technical_drawing

Capture what a manufacturing drawing would say about a part, the dimensions
that matter with their tolerances, the geometric tolerances and datums, the
surface finishes and the inspection steps, as reviewable data pinned to the
CAD model it documents. MetaForge has no 2D drawing generator: this records
the callout data, not a drawing sheet. Say that plainly to the user.

## When to use it

- "Make a drawing spec for the motor mount plate: hole positions +/-0.1,
  flatness 0.05 on the mounting face."
- "Record the surface finish and inspection requirements for the shaft."
- A manufacturing or release phase lists a `technical_drawing` deliverable.

Not for:

- A rendered PDF or DXF drawing sheet: not available. Tell the user.
- Deciding tolerances: that is an engineering decision for the user; use
  `check_tolerance` to evaluate a stack-up the user defined.
- Changing geometry: edit the CAD part (`generate_cad_script`) first, then
  record the drawing against the new revision.

## Tools and profile

| Step | Tool | Profile that serves it |
|---|---|---|
| Find the part | `project.open`, `twin.find_by_property`, `twin.get_node` | every profile |
| Get its file | `twin.stage_work_product_file` | `mechanical`, `simulation`, `robotics` |
| Measure the real geometry | `freecad.describe_step_file`, `freecad.get_properties`, `cadquery.get_properties` | `mechanical` (cadquery also `simulation`) |
| Record the drawing package | `twin.commit_technical_drawing` | only when the server wires it (see below) |
| Fallback record | `twin.record_document` | `core`, full set |
| Record a tolerance rationale | `twin.record_decision` | every profile |

`twin.commit_technical_drawing` is registered only when the server is started
with a technical-drawing recorder, and the standard dev MCP server does not
start one. Check your tool list. If it is absent, use the fallback in step 4
and tell the user that no `TECHNICAL_DRAWING` work product was created.

## Inputs you need before you start

Every value is the user's engineering decision. Ask; never propose
"standard" tolerances or finishes as if they were given.

| Input | Why it matters | Example |
|---|---|---|
| The part | The CAD work product this documents, and its revision | `Motor Mount Plate`, rev 2 |
| Dimensions | Each with `feature`, `nominal_mm`, `tolerance_plus_mm`, `tolerance_minus_mm` | hole spacing 40.0 +0.1 / -0.1 |
| GD&T callouts | `feature`, `symbol` (flatness, position, perpendicularity, ...), `tolerance_value_mm`, `datum_refs` | position 0.1 to A, B |
| Datums | Which faces are A, B, C; a callout with an undefined datum is meaningless | A = mounting face |
| Surface finishes | `feature` and `ra_um` | mounting face Ra 1.6 |
| Inspection requirements | Plain sentences | "CMM check of hole positions, first article" |
| Standard | ASME Y14.5 or ISO GPS; changes how a callout reads | ASME Y14.5-2018 |

At least one dimension is required.

## Procedure

### 1. Find the part and its revision

1. `project.open`, then find the CAD model with
   `metaforge://twin/brief/<project_id>`, `twin.find_by_property` or
   `twin.get_node`. Note the node id and revision.
2. Confirm it is a CAD model, not a drawing or document node.

### 2. Check nominals against the real geometry

1. `twin.stage_work_product_file` with the node id; keep `file_path`.
2. `freecad.describe_step_file` (per part, for an assembly) or
   `freecad.get_properties` / `cadquery.get_properties` with `input_file` =
   that path, for the bounding box and overall size.
3. Compare every overall nominal the user gave (length, width, thickness)
   with the measured box. A drawing that disagrees with its model is worse
   than no drawing. Report each mismatch and ask whether the model or the
   drawing is right; do not silently change either.

Feature-level values (hole spacing, a bore diameter) cannot be measured with
these tools; say they were taken from the user, not verified.

### 3. Record with `twin.commit_technical_drawing` (if listed)

Call it with:

- `name`: `"<part name> Drawing"`
- `part_name`: the part name
- `dimensions`: list of `{feature, nominal_mm, tolerance_plus_mm, tolerance_minus_mm}`
- `gdt_callouts`: list of `{feature, symbol, tolerance_value_mm, datum_refs}`
- `surface_finishes`: list of `{feature, ra_um}`
- `inspection_requirements`: list of strings
- `source_node_ids`: `[<CAD model node id>]`, which links the drawing to the part
- `project_id`

Read the returned `node_id` and the counts, and check the counts match what
you sent.

### 4. Fallback with `twin.record_document`

When `twin.commit_technical_drawing` is not in your list:

1. Write the package as markdown: a header with part name, CAD node id,
   revision and standard; then tables for dimensions, GD&T (with datums),
   surface finishes, and a list of inspection requirements.
2. Call `twin.record_document` with `name` = `"<part name> Drawing"`,
   `document_type: "documentation"`, `content` = the markdown,
   `source_part_node_ids: [<CAD model node id>]`, `project_id`, and
   `metadata` holding the same lists as structured data
   (`dimensions`, `gdt_callouts`, `surface_finishes`,
   `inspection_requirements`, `part_name`, `source_revision`).
3. Tell the user: this is stored as a documentation work product, not a
   `technical_drawing`, so a flow gate that requires a `technical_drawing`
   deliverable will not count it.

If `twin.record_document` is not in your list either (it is on the `core`
profile), give the user the markdown and say nothing was recorded.

### 5. Record decisions and report

- When the user picked a tolerance for a reason (a bearing fit, a mating
  part's tolerance), record it with `twin.record_decision` including the
  alternatives they weighed.
- Report: the node id, which tool recorded it, counts of each callout type,
  every nominal checked against the geometry and its result, and that no
  drawing sheet was produced.

## Checks before you report

- [ ] Every nominal, tolerance, callout and finish came from the user
- [ ] Every datum referenced in a callout is defined
- [ ] Overall nominals compared with the measured geometry; mismatches raised
- [ ] Linked to the exact CAD node (`source_node_ids` or `source_part_node_ids`)
- [ ] Told the user whether a `technical_drawing` or a documentation node was created
- [ ] Told the user no 2D drawing sheet exists

## Failure handling

| Symptom | Likely cause | What to do |
|---|---|---|
| `twin.commit_technical_drawing` not in your list | Server not wired with a drawing recorder | Use step 4; tell the user what was not recorded. |
| `twin.record_document` not in your list | Connected on a profile without it (for example `mechanical`) | Ask for `?profile=core` or no profile, or hand over the markdown. |
| `-32001` naming `freecad` or `cadquery` | Adapter down during the geometry check | Tell the user the nominals are unverified; record only if they agree. |
| Staging fails or returns no file | Node has no stored geometry | Check the node id is the CAD model; ask the user. |
| Nominal disagrees with the measured box | Wrong part, wrong revision, or a units slip (inch vs mm) | Ask which is right; do not record until resolved. |
| Write `approval_required` / held | Writes need a person | Report where it waits; do not retry. |

## Limits

- No rendered drawing, views or title block.
- Values are what the user supplied. Only overall size is checked against the
  model; feature dimensions are not measured.
- A drawing documents one revision. When the part changes, record a new
  drawing against the new revision.
