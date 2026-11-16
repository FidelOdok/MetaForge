---
name: check_cmf_compatibility
description: Check that each part's chosen colour, material and finish (a library appearance) suits what the part is made of, list the finishing processes the choices need, and surface any appearance values still marked as assumptions. Use after choosing finishes for parts, before a render or a manufacturing release, or when the user asks whether a finish works on a material (for example anodising a plastic part).
domain: industrial_design
---

# check_cmf_compatibility

Design-aware aesthetics: a finish is only a choice if it can be put on the
part. The library records which materials each appearance suits; this
checks the chosen pairs against that record.

## When to use it

- After the `colour-material-finish` skill has picked appearances.
- "Can the lid be anodised?" (it can if the lid is aluminium and the
  appearance is recorded for that alloy).
- Before rendering a design in its finishes or releasing it to make.

## Steps

1. For each part, take its material (the library material name the design
   uses) and its chosen appearance.
2. Read each appearance with `library.get_appearance` and pass the records
   in `appearances`.
3. Run the skill. A part fails when its material is not one the appearance
   was recorded for; it is unknown when the appearance records no materials,
   the part has no material or no appearance, or the record was not given.
4. Fix failures by choosing another appearance (`library.list_appearances`,
   filtered by finish) or changing the material; fill unknowns by updating
   the appearance's `materials` with `library.update_appearance` (held for
   the person's approval) when a source says it suits the material.
5. Show the result: `twin.render_work_product` with `appearances` renders the
   parts in their finishes.
6. Record the CMF decision with `twin.record_decision`, listing the
   finishing processes and anything still under review.

## Limits

- Uses only what the library records; there is no built-in table of which
  finishes suit which materials.
- Does not check colour matching between parts or suppliers.

## Checklist

- [ ] every part has a material and an appearance
- [ ] appearances read from the library, not described from memory
- [ ] failures fixed, unknowns sourced or called out
- [ ] finishing processes and review items in the decision
