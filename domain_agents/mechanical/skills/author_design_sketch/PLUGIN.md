---
description: Draw a scaled 2D design sketch of a part or mechanism as a chain of segments (lengths, thicknesses and joint angles in mm and degrees), side by side with the current design when it is a revision, and submit it for a person's approval before any CAD is built. Use when decide_sketch_needed says a sketch is required, when the user asks to see proportions or a layout before modelling, or before revising a committed part, linkage, leg, arm or bracket.
---

# author_design_sketch

Show a person what the part will look like before anyone spends effort in
CAD. The sketch is deliberately simple: each segment of the part (a thigh, a
shin, a bracket arm) is drawn as a bar of its real length and thickness, joined
end to end at its real angle, all at one true scale. For a revision the old
and new chains are drawn side by side at the same scale, so a longer segment
looks longer rather than just being said to be. A dimension table sits
underneath. A person approves it; only then does CAD start.

## When to use it

- `decide_sketch_needed` returned "sketch needed".
- "Show me the leg proportions before you model it."
- "Make the upper arm 60 mm instead of 50, but let me see it first."
- A revision to a committed part that assemblies or analyses depend on.

Not for: the CAD itself (`generate_cad`, `generate_cad_ir`), a manufacturing
drawing with views and tolerances, or a part whose proportions do not reduce
to a chain of segments (a plate with a hole pattern is better shown by its
dimension table alone; say so).

## Tools and profile

| Step | Tool | Served on |
|---|---|---|
| Find the project and the current design | `project.open`, `twin.get_node`, `twin.find_by_property` | every profile |
| Measure the current design (revision) | `twin.stage_work_product_file`, `freecad.describe_step_file` | `mechanical` |
| Submit the sketch for approval | `twin.commit_design_sketch` | only where the server wires a design-sketch recorder |
| Check approval later | `twin.get_node` | every profile |
| Fallback record | `twin.record_document` (`core`), `twin.record_decision` (every profile) | as shown |

`twin.commit_design_sketch` is registered only when the server is started
with a design-sketch recorder. The gateway's own chat harness has it; the MCP
sidecar a plugin connects to does not wire it today. Check your tool list
before you promise the user an approval gate.

## Inputs you need before you start

Ask the user for every number. The sketch exists so a person can check
proportions; drawing your own guesses defeats it.

| Input | Why it matters | Example |
|---|---|---|
| Sketch name | Title of the record | `Leg proportions, rev 2` |
| Subject | What is being sketched | `Front left leg` |
| Proposed segments, in order from the fixed or root end | The drawing itself | Thigh 60 mm x 12 mm, 0 deg; Shin 70 mm x 10 mm, 25 deg |
| Each segment's joint angle | Bend from the previous segment's direction, degrees, 0 = straight on | 25 |
| Current segments (revision only) | Drawn beside the proposal at the same scale | Thigh 50 x 12, Shin 70 x 10 |
| Change notes | Why each change is proposed | "longer thigh for 30 mm more step height" |
| Built nodes this reviews (revision only) | Links the sketch to what it changes | the part's node id |

For a revision, current segment values may come from the committed part:
stage it with `twin.stage_work_product_file` and read the per-body bounding
boxes with `freecad.describe_step_file`. Show the user what you read and get
confirmation before using it; a bounding box is not always a segment length.

## Procedure

### 1. Gather the chain

1. `project.open`; read `metaforge://twin/brief/<project_id>`.
2. Collect the proposed segments (name, `length_mm` > 0, `thickness_mm` > 0,
   `joint_angle_deg`) in order. Segment names must match between current and
   proposed so the table can pair them.
3. For a revision, collect the current segments and the source node ids.

### 2. Draw it

Build one self-contained HTML fragment (inline CSS and inline SVG, no
external assets, no html/head/body wrapper). Use exactly this geometry so the
picture is honest:

1. **Walk the chain.** Start at (0, 0) pointing straight up, which is an angle
   of -90 degrees in screen coordinates (y grows downward). For each segment:
   add its `joint_angle_deg` to the current angle, then the end point is
   start + length x (cos angle, sin angle). The next segment starts there.
2. **One scale for everything.** Collect every point of every chain drawn
   (both chains for a revision, plus the origin). Span = the larger of the x
   range and the y range (at least 1 mm). On a 260 px square canvas with 22 %
   padding each side, scale = 145.6 / span px per mm. Centre on the middle of
   the x and y ranges.
3. **Draw each segment** as a line with round caps, stroke width = the larger
   of 3 px and thickness x scale. A small dark dot marks each joint, a
   coloured dot the free tip. Label each segment at its midpoint with its
   name and length, e.g. Thigh 60mm.
4. **Captions**: a revision gets two panels, "Before" in grey (#6b7280) and
   "After" in blue (#1d4ed8). A new design gets one panel, "Proposed", in
   blue.
5. **Underneath**: a dimension table. Revision columns: segment, length
   before, length after, thickness before, thickness after (a dash for a new
   segment). New design: segment, length, thickness. Then the change notes as
   a bullet list.
6. Title with the sketch name, subject beneath it, then a one-line summary of
   what the sketch checks.

### 3. Submit it

**If `twin.commit_design_sketch` is in your tool list**, call it with:

- `name`: the sketch name
- `html_content`: the fragment from step 2
- `description_text`: the one-line summary
- `source_node_ids`: the built nodes it reviews (omit for a new design)
- `project_id`
- `domain`: mechanical
- `source_tool`: the string author_design_sketch

It returns `node_id`. The sketch starts **unapproved**. Tell the user it is
waiting for their approval in the MetaForge dashboard (the sketch's node,
Approve action) and stop. You cannot approve it and must not look for a way
to. When the user says they approved it, confirm with `twin.get_node` on the
`node_id` that the node's metadata shows approved before starting CAD.

**If it is not in your tool list**, nothing on this connection can record a
design sketch or gate CAD on its approval. Then:

1. Show the user the dimension table (and the SVG, if your client can render
   it) in your reply and ask them to approve or change it there.
2. If `twin.record_document` is in your list, record the table and notes with
   `document_type: "documentation"` and the sketch name. It is stored as
   markdown, so the drawing itself will not render from the twin; keep the
   table.
3. When the user approves in conversation, record their decision with
   `twin.record_decision`: `title` such as "Leg proportions approved for CAD",
   `rationale` in the user's words, `alternatives` they rejected (each as
   `option` plus `reason_rejected`), and `depends_on` with the source node
   ids when it is a revision, so the decision goes stale if that part
   changes again.
4. Tell the user plainly: no design-sketch work product was created, and the
   server does not gate CAD on it.

## Checks before you report

- [ ] Every length, thickness and angle came from the user (or was read from
      the model and confirmed by them)
- [ ] One shared scale for before and after
- [ ] Segment names match across before and after
- [ ] The sketch is submitted, or the user was told it could not be
- [ ] You did not start CAD before a person approved

## Failure handling

| Symptom | Likely cause | What to do |
|---|---|---|
| `twin.commit_design_sketch` missing | Server not wired with a sketch recorder | Use the fallback above and say what was not recorded. |
| Commit held for approval | Writes need a person on this connection | Tell the user where it waits; do not retry or reword. |
| Rejected: name or html_content required | Empty field | Fill it; do not send a placeholder name. |
| Node never shows approved | Nobody has approved yet | It is waiting, not failed. Do not poll repeatedly; ask the user. |
| `-32001` naming `freecad` while reading the current design | Adapter down | Ask the user for the current values instead. |

## Limits

- Segments are straight capsule bars. The sketch shows proportions and
  topology, not manufacturing detail, holes or fillets.
- One planar chain per panel. Branching mechanisms (a hand with several
  fingers) need one sketch per chain.
- The approval is a person's. A sketch you submit is never approved by you.
