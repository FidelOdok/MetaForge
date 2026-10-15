---
description: Work out a product's form factor, the smallest outer envelope its contents need, from the internal components (positioned or stacked), the internal clearance and the wall thickness, and check it against a required maximum size. Use when the user asks how big a product or enclosure has to be, whether the contents fit a target size, or what envelope to design the housing around, before mechanical CAD starts.
---

# form_factor_envelope

Industrial design decides the form factor before mechanical design draws
the housing (the Planner puts Industrial Design ahead of Mechanical for this
reason). This skill gives the hard floor under that decision: what the
contents need, plus clearance and wall.

## When to use it

- "How small can this enclosure be?"
- "Will the battery, board and speaker fit a 70 x 40 x 20 mm pocket size?"
- Before authoring an enclosure, to fix its outer dimensions.

Not for: whether a person can hold it (`check_ergonomics`), or how it
looks (`colour-material-finish`).

## Gather the inputs, each with its source

| Input | Where it comes from |
|---|---|
| component sizes | a part in the twin (`twin.get_node`, its bounding box): `basis: measured`; a datasheet: `basis: datasheet`; the person: `basis: stated` |
| positions | the layout the person or the design gives; otherwise a stacking axis and gap |
| wall thickness | the chosen process's minimum wall: `library.process_limits` (or `library.machine_limits` for a library machine) |
| clearance | the person, or a recorded design value (the `source-engineering-values` skill) |
| maximum envelope | the requirement that states it (`twin.find_by_property` on the project's constraints) |

Every clearance, wall and maximum needs a `source_ref`. If one is missing,
find it with `source-engineering-values` or ask; do not pick a number.

## Steps

1. Collect the components and decide positioned or stacked.
2. Run the skill.
3. Read the result: the outer dimensions, the fill ratio (a low ratio means
   wasted volume, a ratio near 1 leaves no room for cables and fixings),
   any overlaps, and the fit against the maximum.
4. Record the chosen envelope as a decision with `twin.record_decision`,
   naming the sources it rests on, so mechanical design works to it.

## Limits

- Axis-aligned boxes: no rounded corners, draft, ribs, bosses or cable
  routes. Treat the result as a minimum, not the final housing.
- The fit check needs a sourced maximum; without one it is not run.

## Checklist

- [ ] every component's size has its basis
- [ ] wall from the process, clearance and maximum with their sources
- [ ] overlaps resolved before the envelope is used
- [ ] envelope recorded as a decision
