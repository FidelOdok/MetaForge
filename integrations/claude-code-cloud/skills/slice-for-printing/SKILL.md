---
name: slice-for-printing
description: Slice a model for FDM 3D printing with PrusaSlicer to get its real print time, filament use and weight, and price printing it at the Slant 3D print farm. Use when the user asks how long a part takes to print, how much filament or material it needs, to compare print settings (layer height, infill, supports, material), wants G-code for their printer, or asks what a print farm would charge. Do not use for CNC, sheet metal or PCB quotes (that is quote-and-order).
---

# Slice for printing

`slicer.slice` runs PrusaSlicer in its own container and reads the estimates
from the G-code it writes. Its numbers are the slicer's, not guesses: quote
them as such. `slant3d.*` asks the Slant 3D farm to slice and price the same
part when `SLANT3D_API_KEY` is set.

## Rules

1. **Slice a real file.** The input is an STL, 3MF, OBJ or STEP on the adapter
   workspace, in millimetres. For a part in the twin, get its path with
   `twin.stage_work_product_file`. A STEP from CAD slices directly; a mesh
   from `mesh-from-prompt` is already a file path.
2. **Say which printer the numbers are for.** The built-in printers
   (`slicer.list_profiles`) are generic Marlin machines by bed size. Their
   start G-code is generic: tell the person not to print it as is on a
   machine with its own start sequence. For their real printer, ask for a
   config exported from PrusaSlicer (File, Export, Export Config) on the
   workspace and pass it as `config_path`.
3. **A refusal is an answer.** The slicer refuses a part that does not fit
   the bed, copies that do not all fit, and any config with a post-processing
   script. Report the reason; do not shrink the part or drop copies silently.
4. **A farm quote is print cost only.** Slant 3D prices exclude shipping and
   tax, and `slant3d.analyze_part` sends the file to Slant 3D, where it stays
   on the account. Say both. There is no order tool: ordering happens on
   Slant 3D's site, by the person.

## Steps: estimate with the slicer

1. `slicer.list_profiles` if the person has not named a printer and
   material. Defaults: `generic_220`, PLA, 0.2 mm layers, 20% infill, two
   perimeters, no supports.
2. `slicer.slice` with `file_path`, plus `printer` and `material` or
   `config_path`, and any of `layer_height`, `infill_percent`, `perimeters`,
   `supports`, `brim_mm`, `nozzle_mm` and `copies`.
3. Report `print_time`, `filament_g`, `filament_mm`,
   `layers` and the settings used, and the `gcode_path` it wrote. To compare
   options, slice once per option and give a small table.
4. If the person wants the result kept, record the estimate with
   `twin.record_document` against the part (the figures, settings and
   slicer version). The G-code file itself cannot be recorded as a twin work
   product yet, so `library.dispatch_job` cannot send it; the person copies
   it from the workspace to their printer.

## Steps: price at Slant 3D

1. The part must be an STL. Convert a STEP with `cadquery.export_geometry`
   or `zoo.convert`, or a GLB with `tripo.convert`.
2. `slant3d.analyze_part` with `file_path`. It returns a `file_id` with the
   farm's own size, weight, print time and whether it needs supports.
3. `slant3d.list_filaments` to pick a material and colour that is in stock.
4. `slant3d.quote` with `file_id`, `filament_id` and `quantity`. Report the
   per-unit price, any multi-body or oversize surcharge, and the total in
   USD.

## Checklist

- [ ] printer and material named with the estimate
- [ ] generic start G-code flagged when a built-in printer was used
- [ ] refusals reported, not worked around
- [ ] farm quote stated as print cost only, excluding shipping and tax
