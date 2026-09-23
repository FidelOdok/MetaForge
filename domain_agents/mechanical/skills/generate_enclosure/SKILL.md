# generate_enclosure

Generate a PCB enclosure from board dimensions, connector cutouts, and mounting holes using CadQuery.

## What it does

1. Takes PCB dimensions, connector cutout specs, and mounting hole positions as input
2. Invokes the `cadquery.generate_enclosure` MCP tool to create a parametric enclosure
3. Persists it into the Twin via `twin.commit_geometry` (unless `commit=false`)
4. Returns the STEP file path, internal volume, external dimensions, mounting info, and commit result

## Tools Required

- `cadquery.generate_enclosure` -- CadQuery PCB enclosure generation
- `twin.commit_geometry` -- persistence (best-effort; failure is reported on the output, not raised)

## Input

- `work_product_id` -- UUID of an existing enclosure work_product to link to, e.g. a PCB
  work product an Electronics Agent already created (optional -- `commit_geometry` creates
  a fresh `CAD_MODEL` work product when omitted, same as `generate_cad`)
- `pcb_length` -- PCB length in mm
- `pcb_width` -- PCB width in mm
- `pcb_thickness` -- PCB thickness in mm (default: 1.6)
- `component_max_height` -- Max component height above PCB in mm (default: 10.0)
- `connector_cutouts` -- List of cutout definitions (width, height, x, z, side)
- `mounting_holes` -- List of mounting hole positions (x, y, diameter)
- `wall_thickness` -- Enclosure wall thickness in mm (default: 2.0)
- `material` -- Material name (default: ABS)
- `project_id` -- Project UUID to link the resulting work product to, when committed
- `commit` -- Persist into the Twin immediately (default: true)

## Output

- `cad_file` -- Path to the generated enclosure STEP file
- `internal_volume` -- Internal volume in mm^3
- `external_dimensions` -- External length, width, height
- `mounting_info` -- Hole count and cutout count
- `material` -- Material used
- `committed` -- Whether the geometry was persisted into the Twin
- `twin_node_id` -- Twin node ID of the committed `cad_model`, when committed
- `model_url` -- Viewer URL of the committed `cad_model`, when committed
- `commit_error` -- Set when `commit=true` was requested but persistence was skipped or failed

## Cross-Domain Usage

This is a cross-domain skill: the Electronics Agent (via KiCad) provides PCB
dimensions and connector positions, and the Mechanical Agent generates the
matching enclosure. The Digital Twin mediates the data exchange.

## Limitations

- Generates simple box enclosures with shell (no complex organic shapes)
- Cutouts are rectangular only
- No snap-fit or screw-boss features yet (Phase 3)
- Phase 2 skill
