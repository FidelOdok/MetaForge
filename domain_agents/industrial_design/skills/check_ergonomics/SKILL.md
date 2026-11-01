# check_ergonomics

Checks the features a person holds, presses, reaches or lifts against limits for the intended users.

## What it does

1. Takes the product, its intended users, its human-contact features and the limits for those users
2. Checks each feature against every limit of its kind: grip diameter and width, handle length, finger clearance, button size (mm), actuation force (N), operating height, reach (mm), hand-held mass (kg)
3. Reports pass, fail or unknown with the margin, the limit's population and source
4. Gives a verdict: does_not_fit (any fail), incomplete (any unknown, or nothing checked), or fits
5. Lists limits stated for a different population than the intended users

## Tools Required

None -- pure computation. No anthropometric figures are built in: each limit comes with its population and source (a cited dataset such as ISO 7250 tables, or published ergonomics guidance).

## Input

- `product`, `user_population`
- `features` -- `{name, kind, value, basis: measured | declared | stated}`
- `limits` -- `{kind, min | max, population, source_ref}`

## Output

`verdict`, `findings`, `passed`, `failed`, `unknown`, `unknowns`, `population_mismatches`

## Limitations

- Static dimensional and force checks only: no posture, repetition or comfort modelling
- A limit for the wrong population is reported, not corrected
