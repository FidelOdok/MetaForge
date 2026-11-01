---
name: manage-processes-and-materials
description: Record and read manufacturing processes (CNC milling, FDM, laser cutting, sheet-metal bending, moulding, PCB fabrication) with their design rules and achievable tolerance, and materials with their properties, in the MetaForge library, with a source for every value; and use them in a design flow. Use when the user names a material or process they want to use, asks what a process can hold or what a material's properties are, wants DFM limits for a process and material, or when a manufacturing phase needs sourced process rules or material properties.
---

# Manage processes and materials

The library holds what a manufacturing technique can do and what a material
is, once, for every project. Process ranking, the DFM review and the flow
generator read them, so a design is checked against recorded, sourced
figures rather than numbers from memory.

- A **process** (`cnc_milling`, `fdm`, `laser_cutting`) has a family, the
  machine kinds that run it, the material families it can make, and
  **rules**: minimum wall, hole, inside corner and bend radii, draft,
  overhang, feature size, envelope and achievable tolerance. A rule can apply
  to one material family only (a metal wall differs from a plastic one).
- A **material** (`aluminium_6061_t6`) has a family, a designation (the
  standard grade name), forms, the processes it suits, and **properties**:
  density, stiffness, yield and ultimate strength, fatigue, elongation,
  thermal figures, cost per kg (with the currency in the unit), and the
  across-the-grain pair for woods and sheet goods. Woods and panels have no
  yield point: record their bending strength (`bending_strength_mpa`, and
  `bending_strength_across_mpa`) and tension across the grain
  (`tensile_strength_across_mpa`), never as yield.

## Rules

1. **Every value has a source.** `standard`, `datasheet`,
   `manufacturer_spec`, `supplier` and `measurement` need a reference (the
   standard and clause, the datasheet URL, the test note). `user` means the
   person said so. `assumption` is allowed only when the person accepts it,
   and it is flagged for review everywhere it is used. Leave out what you
   cannot source; it then reads as unknown.
2. **Look before adding.** `library.list_processes` and
   `library.list_materials` first; reuse an existing entry rather than
   adding a second `aluminium_6061_t6`.
3. **Writes are held.** `library.register_process`, `register_material` and
   the `update_*` tools wait for the person's approval. Say so and stop.
4. **Names are identifiers.** Lower case, digits, `_` or `-`
   (`stainless_304`, `sheet_metal_bending`). The `title` is the readable name.

## Recording an entry

1. Find the source: the material's datasheet or the standard it is made to
   (`web.search`, `web.fetch`); for a process, a recognised design guide,
   a standard, or the shop's own published rules.
2. Register it with each value as `{value, unit, source_kind, source_ref}`.
   Property units are fixed (`kg/m3`, `MPa`, `%`, `W/mK`, `um/mK`, `C`;
   `cost_per_kg` as `GBP/kg` or similar); rule units are `mm` or `deg`.
3. Confirm with `library.get_process` or `library.get_material` once
   approved.

## Using them in a design

- In a design flow, pass `manufacturing_context.process_ids` and
  `material_ids` to `flow.propose`. Their rules and properties go into every
  phase's context. With none chosen, the context lists entries that match
  the stated processes, the library machines' processes and the stated stock
  materials.
- `library.process_limits` with a process and a material returns the rules
  that apply as `check_manufacturability` limits and a
  `rank_manufacturing_processes` candidate, each citing its source. A process
  not recorded as making that material's family is refused.
- Use the library material's properties for mass, stiffness and strength
  checks, citing the entry, rather than typing a density or a yield
  strength again.

## Checklist

- [ ] looked for an existing entry first
- [ ] every value sourced, or left out
- [ ] told the person the write is waiting for their approval
