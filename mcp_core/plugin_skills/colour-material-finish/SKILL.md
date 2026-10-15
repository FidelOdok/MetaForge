---
name: colour-material-finish
description: Choose and record a part's colour, material and finish (CMF) in the MetaForge library, with the colour named in its colour system and every value sourced. Use when the user picks or asks about a colour, a finish (anodised, powder coated, painted, moulded-in colour, bead blasted, polished) or a texture for a part, or wants a render to show the real finish.
---

# Colour, material and finish

An **appearance** in the library is what an industrial designer specifies
for a surface: the finish, the colour, the texture, a few measurable surface
properties and the library materials and processes it applies to.

- **Colour** has two halves. Its *name* in a colour system (`ral` with a
  code like `RAL 9005`, `pantone`, `ncs`, a `supplier`'s own code, or
  `custom`) is what a coater or moulder works to. Its `srgb_hex` is only how
  it is drawn on a screen. A screen value for a RAL or Pantone code is an
  approximation, so it carries its source like any other value.
- **Properties**: `gloss_gu_60` (GU, ISO 2813 at 60 degrees),
  `coating_thickness_um` and `surface_roughness_ra_um` (um) describe the
  part; `render_roughness` and `render_metallic` (0 to 1) describe how a
  renderer draws it and are usually the user's choice or an assumption.
- **Texture** is a standard reference where there is one (for example
  `VDI 3400 Ref 27`, or a texture supplier's code).

## Rules

1. **Look before adding.** `library.list_appearances` (narrow by `family`,
   which is the finish); reuse an existing entry.
2. **Never invent a colour value.** Take the code and the screen value from
   the user, the supplier's colour card or a cited conversion table, and
   record where it came from (`source_kind`, `source_ref`). A value you
   chose for a concept is `assumption` and is flagged for review.
3. **Say which materials and processes it is for** (`materials`,
   `processes`, library names), so an anodised finish is not proposed for a
   polymer part.
4. **Register with `library.register_appearance`**, change with
   `library.update_appearance`; both are held for the person's approval and
   keep the old version.

## Seeing it

`twin.render_work_product` with `appearances` (part name to appearance name)
renders a work product with each part in its appearance: the screen colour,
`render_roughness` and `render_metallic` go to Blender, and the render records
which appearance and version it used. A part name that matches nothing comes
back in `unmatched_colours`.

## Checklist

- [ ] looked for an existing appearance
- [ ] colour named in its system, with a sourced screen value
- [ ] materials and processes named
- [ ] assumed values left flagged for review
