# form_factor_envelope

The smallest outer envelope a product needs to hold its contents.

## What it does

1. Takes the internal components as boxes: positioned (their minimum corner in the product's frame) or stacked along an axis with a gap
2. Adds the internal clearance on every side, then the wall thickness
3. Reports the contents, inner and outer dimensions, volumes and fill ratio, and any positioned components that overlap
4. With a sourced maximum envelope, says whether the product fits in any orientation, and by how much

## Tools Required

None -- pure computation. Component sizes come from the twin (`twin.get_node` bounding boxes), datasheets or the person; the wall usually comes from the chosen process's minimum wall (`library.process_limits`).

## Input

- `components` -- `{name, dims_mm, position_mm?, basis: measured | datasheet | stated}`
- `stack_axis`, `gap_mm` -- for unpositioned components
- `clearance`, `wall_thickness` -- `{value_mm, source_ref}`
- `max_envelope` -- `{dims_mm, source_ref}` (optional)

## Output

`layout`, `contents_dims_mm`, `inner_dims_mm`, `outer_dims_mm`, volumes, `fill_ratio`, `fits`, `margin_mm`, `overlaps`, `basis`, `sources`, `messages`

## Limitations

- Axis-aligned boxes only: no rounded corners, draft or cable routing
- Fit is checked only against a stated, sourced maximum
