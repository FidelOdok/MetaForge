---
name: convert-cad-files
description: Convert a supplier's or customer's CAD file (SolidWorks, CATIA, NX, Creo, Inventor, Parasolid, ACIS, STEP) or a mesh (STL, OBJ, PLY, FBX, glTF) to STEP, STL, GLB, glTF, OBJ, PLY or FBX with Zoo's cloud API, and measure its volume, surface area, centre of mass, bounding box and mass. Use when the user has a native CAD file MetaForge cannot read, needs an STL for a printer, or asks what a part weighs. Do not use to edit geometry (that is CAD authoring).
---

# Convert CAD files

`zoo.*` sends a file on the adapter workspace to Zoo's cloud API when the
deployment has `ZOO_API_TOKEN` set. Conversions are charged to the Zoo
account: say so before converting a batch.

## Rules

1. **The file is uploaded to Zoo.** Tell the person before sending a
   customer's or supplier's file that may be under NDA.
2. **A mesh converted to STEP is still a mesh.** STL, OBJ and similar become a
   faceted solid, not editable CAD. Say so when asked for STEP from a mesh.
3. **Check scale before trusting a mass.** An STL carries no units. Look at
   the bounding box from `zoo.mass_properties` and confirm it matches the
   part's real size in the `length_unit` given (default mm).

## Steps

1. `zoo.convert` with `file_path` and `output_format` (`step` to bring
   native CAD into MetaForge, `stl` for a printer).
2. A file over 25 MB is queued: the result has `status: in_progress` and an
   `operation_id`. Call `zoo.get_conversion` with it until it completes.
3. Keep the result: `twin.commit_geometry` with the returned `file_path`
   unchanged, `format` set to the output format, and `source_tool`
   `zoo.convert`.
4. For mass, `zoo.mass_properties` with `file_path`, `length_unit`, and
   `material_density` in kg/m3 (for example 2700 for aluminium, 7850 for
   steel, 1240 for PLA). Name the density you used and where it came from.

## Checklist

- [ ] the person knows the file goes to Zoo
- [ ] converted file committed with `source_tool` `zoo.convert`
- [ ] bounding box checked before quoting a mass
