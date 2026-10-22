---
name: check_ergonomics
description: Check whether people can hold, grip, press, reach and lift a product, by comparing its human-contact features (grip diameter and width, handle length, finger clearance, button size, actuation force, operating height, reach, hand-held mass) with limits from a cited anthropometric dataset or ergonomics guidance for the intended users. Use when the user asks if a handle, grip, button or product is comfortable or usable, or for an ergonomics review of a design.
domain: industrial_design
---

# check_ergonomics

Ergonomics in MetaForge is a check against recorded limits, not an opinion.
The skill holds no body measurements of its own: every limit arrives with
the population it describes and where it came from.

## When to use it

- "Is a 45 mm handle too thick?"
- "Can a small hand reach the trigger?"
- An ergonomics review before the design is frozen.

Not for: the product's overall size (`form_factor_envelope`), finishes
(`colour-material-finish`), or posture and repetitive-strain studies, which
this does not model.

## Who the users are

Start from the requirement: who uses the product (for example "UK adults,
5th percentile female to 95th percentile male", or "children 6 to 10").
If no requirement says, ask the person; the users are their decision. Pass
it as `user_population`.

## Finding the limits

Each limit is `{kind, min, max, population, source_ref}`. Look in the library
first: `library.list_ergonomic_limits` with `family` set to the feature kind
returns recorded limits, each citing its standard and clause (the seed is
MIL-STD-1472H, 2020: push buttons, toggle switches, bar handles, handle
diameter by item weight, joystick grips, work surface heights). Pick the
entry whose `applies_to` matches the feature (a fingertip push button, not a
palm one) and pass it as it is: the skill reads a library entry directly and
cites its name and version. MIL-STD-1472H describes US military users, so
for other users the skill lists it under `population_mismatches`; say so.
When the library has nothing for a feature, find it with the
`source-engineering-values` skill, in its order: the person, the project's
documents (`knowledge.search`), then a standard or reference (`web.search`,
`web.fetch`): anthropometric tables such as ISO 7250 or a national survey,
or published ergonomics guidance for grip sizes and forces. Cite the table,
the measure and the percentile you used. Read the figure; do not recall it.

A limit for a different population than the users is still reported, and
the skill lists it under `population_mismatches`. Say so in the review
rather than treating it as a match.

## Steps

1. State the users.
2. Collect the features and their values, with their basis: measured from
   the model (`twin.get_node`, `freecad.measure`), declared at authoring, or
   stated.
3. Find a sourced limit for each feature kind.
4. Run the skill; read the verdict, margins, unknowns and mismatches.
5. Record failing and unknown findings with `twin.record_evidence` against
   the cad_model, and the review with `twin.record_decision`.

## Limits

- Static size, force and mass checks only.
- No figure is supplied for you; an unsourced limit is not accepted.

## Checklist

- [ ] users stated, from the requirement or the person
- [ ] library limits used where one matches; new ones recorded with
      `library.register_ergonomic_limit` (held for the person's approval)
- [ ] every limit cites its dataset or guidance, measure and percentile
- [ ] population mismatches called out
- [ ] findings recorded as evidence
