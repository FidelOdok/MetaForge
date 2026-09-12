# decide_sketch_needed

Deterministic gate: decides whether a `design_sketch` work product must be
authored (`twin.commit_design_sketch`) and approved before any CAD/build
tool runs. Call before `cadquery.*`/`freecad.*`.

## Rules (any one -> `sketch_needed = true`)

1. `user_requested_review`.
2. `source_node_ids` non-empty -- revising a built design.
3. `part_count > 1` and `has_moving_joints` -- kinematic assembly.
4. `topology_is_novel` -- no prior built reference.
5. `part_count >= 4` -- assembly complexity.

None fire -> proceed to CAD directly.

## Input

`source_node_ids`, `part_count`, `has_moving_joints`, `topology_is_novel`,
`user_requested_review`.

## Output

`sketch_needed`, `reasons`, `is_revision`, `recommended_source_node_ids`.

## Limitations

Pure classification, no geometry/Twin reads. Coarse by design.
