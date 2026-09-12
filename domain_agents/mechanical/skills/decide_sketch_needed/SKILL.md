# decide_sketch_needed

Deterministic gate: decides whether a `design_sketch` work product must be
authored (`twin.commit_design_sketch`) and human-approved (`POST
/nodes/{id}/approve-sketch`) before any real CAD/build tool is invoked.

Not robot-specific -- this applies to any CAD work: a single bracket, a
multi-part enclosure, a kinematic assembly, or a revision to something
already built. Call this first, before `cadquery.*`/`freecad.*` authoring
tools, whenever the work is non-trivial or touches an already-built design.

## What it does

Evaluates a fixed set of rules against the shape of the proposed action (part
count, moving joints, revision vs. brand-new, topology novelty, explicit
request) and returns a pass/fail-style decision with the reason(s) it fired.
It never calls MCP tools or reads the Twin -- the decision is a pure function
of its input, so it is safe to call cheaply and often.

## Rules (any one triggers `sketch_needed = true`)

1. **Explicit request** -- `user_requested_review = true`.
2. **Revision of a built design** -- `source_node_ids` is non-empty. Changing
   already-committed geometry (a shipped CAD model, a robot description)
   carries more risk than a from-scratch design, so it always needs a
   reviewed reference first.
3. **Kinematic assembly** -- `part_count > 1` and `has_moving_joints = true`.
   This is the exact failure class behind the original "hyperrealistic
   quadruped" defect: a multi-link mechanism authored straight to CAD with no
   proportions/topology check produced a shape that didn't read as a real
   quadruped.
4. **Novel topology** -- `topology_is_novel = true`: no prior built reference
   for this shape/mechanism exists in the project, so there's nothing to
   sanity-check proportions against except a sketch.
5. **Assembly complexity** -- `part_count >= 4`, regardless of joints (layout
   and interference risk scale with part count on its own).

If none of the above apply -- a simple, brand-new, low-part-count design with
no moving joints and no novel topology -- the gate passes with
`sketch_needed = false` and the caller proceeds directly to CAD authoring.

## Input

- `source_node_ids` -- existing Twin work-product IDs this action would
  modify (empty for a brand-new design)
- `part_count` -- number of distinct parts/links/bodies involved
- `has_moving_joints` -- whether kinematic joints connect the parts
- `topology_is_novel` -- whether no prior built reference exists for this
  shape/mechanism in the project
- `user_requested_review` -- explicit human ask, regardless of other signals

## Output

- `sketch_needed` -- the decision
- `reasons` -- every rule that fired (or the pass-through reason if none did)
- `is_revision` -- whether this action modifies an already-built design
- `recommended_source_node_ids` -- pass straight through to
  `twin.commit_design_sketch`'s `source_node_ids` when `sketch_needed` is true

## Follow-up when `sketch_needed` is true

Call `twin.commit_design_sketch` with a self-contained HTML reference sketch
before authoring real geometry, then wait for `metadata.approved` to flip
true (dashboard "Approve" action, or the `approve-sketch` REST route) before
proceeding to `cadquery.*`/`freecad.*` build tools.

## Limitations

- Deliberately conservative and coarse-grained: it flags entire classes of
  work (any revision, any kinematic assembly, any 4+ part design), not
  fine-grained per-feature risk. False positives (a sketch requested for
  something that turns out trivial) are cheap; false negatives are not.
- Does not inspect actual geometry or query the Twin -- callers are
  responsible for supplying accurate `part_count`/`has_moving_joints`/
  `topology_is_novel` signals.
