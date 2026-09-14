# author_design_sketch

Deterministically renders a scaled 2D kinematic-chain diagram -- segments
drawn end-to-end, proportioned and angled from real mm values -- and
persists it as a `DESIGN_SKETCH` work product via `twin.commit_design_sketch`.

v1 of this skill only tabulated before/after numbers ("50mm -> 60mm");
that's data, not a sketch -- a reviewer can't see what the part looks like
from a table. v2 actually draws it: a chain of linked segments (e.g. thigh
-> shin -> foot) rendered as a proportioned diagram, with the dimension
table kept underneath as supporting detail.

## What it does

1. Takes a subject and a chain of segments (`name`, `length_mm`,
   `thickness_mm`, `joint_angle_deg` relative to the previous segment).
2. Walks the chain into 2D points and fits one shared px-per-mm scale
   across before/after so a size difference is drawn honestly, not just
   stated.
3. Renders each chain as an SVG: a line per segment (width = thickness,
   rounded caps), a joint dot between segments, and a length label.
4. When `before_segments` is given (a revision), draws Before/After
   side by side at the same scale. When empty (a brand-new design,
   nothing built yet), draws a single "Proposed" diagram.
5. Adds a dimension table below the diagram(s), matched by segment name.
6. Calls `twin.commit_design_sketch` with the rendered HTML.

## Input

`name`, `subject_name`, `summary`, `after_segments` (required -- the
proposed chain), `before_segments` (empty for a brand-new design),
`change_notes`, `source_node_ids`, `project_id`, `domain`.

## Output

`node_id`, `segment_count`, `is_revision`, `approved` (always `false`).

## Limitations

Segments are drawn as straight capsule bars, not real part geometry --
this communicates proportions and topology (lengths, thicknesses, joint
angles), not manufacturing detail. A single segment still renders as one
bar (not multi-segment-only); works for anything from a simple bracket to
a multi-joint limb.
