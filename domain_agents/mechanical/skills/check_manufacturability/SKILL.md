# check_manufacturability

Design-for-manufacture (DFM) review of a finished design against the chosen process's limits.

## What it does

1. Takes the chosen process, the design's features (with how each value is known) and the process's sourced limits
2. Checks each feature against every limit of its kind: wall thickness, hole diameter, inside corner radius, bend radius, draft angle, overhang angle, feature size, envelope
3. Reports pass, fail or unknown per feature, with the margin and the limit's source
4. Gives a verdict: not_manufacturable (any fail), incomplete (any unknown, or nothing checked), or manufacturable

## Tools Required

None -- pure computation. Feature values come from the geometry (freecad.measure, the overhang metric) or from what the part was authored with. A library machine's limits come from library.machine_limits.

## Input

- `process` -- the process chosen at the route gate
- `features` -- `{name, kind, value | dims_mm, basis: measured | declared | stated}`
- `limits` -- `{kind, min | max | max_dims_mm, source_ref}`; every limit carries its source
- `work_product_id` -- the cad_model checked (optional)

## Output

- `verdict` -- manufacturable / not_manufacturable / incomplete
- `findings` -- per feature: status, value, limit, margin, basis, limit source, message
- `passed`, `failed`, `unknown`, `unknowns`

## Rules

- A feature with no sourced limit, or no value, is unknown -- never a pass
- The basis (measured / declared / stated) is reported with every finding

## Limitations

- MetaForge cannot yet measure wall thickness, holes, radii or draft from geometry; those values are declared or stated
- No GD&T, surface finish or assembly-access checks
