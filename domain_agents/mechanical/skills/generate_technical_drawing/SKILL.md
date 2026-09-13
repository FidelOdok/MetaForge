# generate_technical_drawing

Persists a structured drawing-package spec for a CAD part as a
`TECHNICAL_DRAWING` work product via `twin.commit_technical_drawing`,
linked back to its source `CAD_MODEL`.

**Not a rendered 2D vector drawing.** MetaForge has no TechDraw-equivalent
generator wired up -- this persists the callout DATA a real drawing would
encode (dimensions, GD&T, finishes, inspection notes), reviewable on its
own, not a substitute for an actual drawing sheet.

## What it does

1. Takes a source `work_product_id` (the `CAD_MODEL` this drawing
   documents), dimensions, GD&T callouts, surface finishes, and inspection
   requirements.
2. Verifies the source CAD_MODEL exists in the Twin.
3. Calls `twin.commit_technical_drawing`, which renders the tables and
   links a `PARENT_OF` edge back to the source part.

## Input

`work_product_id`, `part_name`, `dimensions` (feature, nominal_mm,
tolerance_plus_mm, tolerance_minus_mm), `gdt_callouts` (feature, symbol,
tolerance_value_mm, datum_refs), `surface_finishes` (feature, ra_um),
`inspection_requirements`.

## Output

`node_id`, `dimension_count`, `gdt_callout_count`, `surface_finish_count`.

## Limitations

Dimensions/callouts are caller-supplied, not derived from the CAD geometry
itself -- pair with `freecad.measure`/`cadquery.get_properties` upstream if
you need the actual measured values rather than design intent.
