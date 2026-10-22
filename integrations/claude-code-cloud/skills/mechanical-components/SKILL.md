---
name: mechanical-components
description: Choose and record standard and purchased mechanical parts (screws, nuts, washers, bearings, standoffs, rails, extrusions, hinges, actuators) in the MetaForge library, with every dimension and rating sourced. Use when a design needs fasteners or bearings, when the user names a standard part ("M3 x 10 cap screw", "608 bearing"), or asks which bearing or screw fits. Standard parts are built from their standard's table; nothing is sized from memory.
---

# Mechanical components

Mechanical parts live in the library like machines, processes and
materials. Two kinds:

- **Standard parts** are built from their standard's dimension table:
  ISO 4762 socket head cap screws, ISO 4032 hex nuts, ISO 7089 plain
  washers and deep groove ball bearings (by designation, such as 608). Each
  dimension cites the table row; a bearing's load ratings, speeds and mass
  are a catalogue's, as tabulated, and the entry says to confirm them
  against the chosen maker's datasheet.
- **Purchased parts** are a supplier's part with its own CAD (a STEP URL with
  its licence, or a STEP work product) and the supplier's part numbers.

## Rules

1. **Look before adding.** `library.list_components` (narrow by `family`);
   reuse an existing entry.
2. **Build standard parts from the table.** `library.standard_part` with the
   family (`iso4762`, `iso4032`, `iso7089`, `deep_groove_bearing`), the size
   and, for a screw, `length_mm`. It stores nothing and refuses a size or
   length the standard does not have. Register what it returns with
   `library.register_component` (held for the person's approval). Do not type
   a standard part's dimensions yourself.
3. **Ratings decide, with their source.** Pick a bearing on its
   `dynamic_load_kn`, `static_load_kn` and `limiting_speed_rpm` against the
   design's loads and speeds; state the margin and that the ratings are
   catalogue values to confirm with the maker.
4. **Purchased parts carry their CAD licence.** Record where the STEP came
   from and its licence (for example step.parts, a maker's site, or the
   FreeCAD Parts Library under CC-BY-3.0 with its author).
5. **Sourcing is a list of supplier part numbers** (`suppliers`), which the
   distributor tools (`distributors.resolve_offers`) price.

## Checklist

- [ ] looked for an existing entry
- [ ] standard parts built with `library.standard_part`, not typed
- [ ] bearing chosen on rated load and speed with a stated margin
- [ ] purchased CAD has its source and licence recorded
