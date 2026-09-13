# author_design_sketch

Deterministically renders a styled before/after comparison sketch and
persists it as a `DESIGN_SKETCH` work product via `twin.commit_design_sketch`.

Fills the gap between `decide_sketch_needed` (decides IF a sketch is
needed) and the raw commit tool (which only stores whatever HTML a caller
hands it, with no structure enforced). Without this skill, a chat agent
free-writes arbitrary unstyled prose HTML per call -- exactly what a
design sketch should not be: a consistent, reviewable proportions/topology
reference, not a paragraph.

## What it does

1. Takes a subject, a summary, and a list of proposed changes (feature,
   before, after, rationale).
2. Renders a styled HTML table -- when both `before`/`after` parse as a
   leading numeric magnitude, adds a simple two-bar visual comparison
   (no chart library, just CSS widths).
3. Calls `twin.commit_design_sketch` with the rendered HTML.

## Input

`name`, `subject_name`, `summary`, `proposed_changes` (feature, before,
after, rationale), `source_node_ids` (existing work products this sketch
reviews -- the revision case), `project_id`, `domain`.

## Output

`node_id`, `change_count`, `approved` (always `false` on creation).

## Limitations

The before/after bar is a rough visual aid from parsed leading numbers, not
a scaled engineering drawing -- mixed units in one comparison (e.g. "50 mm"
vs. "2 in") are not normalized. Rows without a numeric magnitude (e.g. "add
a guard") render without a bar.
