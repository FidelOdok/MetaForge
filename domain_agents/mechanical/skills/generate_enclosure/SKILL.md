# generate_enclosure

Generate a PCB enclosure from board dimensions, connector cutouts, and mounting holes using CadQuery.

## What it does

1. Takes PCB dimensions, connector cutout specs, and mounting hole positions as input
2. Invokes the `cadquery.generate_enclosure` MCP tool to create a parametric enclosure
3. Returns the STEP file path, internal volume, external dimensions, and mounting info

## Tools Required

- `cadquery.generate_enclosure` -- CadQuery PCB enclosure generation

## Input

- `work_product_id` -- UUID of the enclosure work_product in the Digital Twin
- `pcb_length` -- PCB length in mm
- `pcb_width` -- PCB width in mm
- `pcb_thickness` -- PCB thickness in mm (default: 1.6)
- `component_max_height` -- Max component height above PCB in mm (default: 10.0)
- `connector_cutouts` -- List of cutout definitions (width, height, x, z, side)
- `mounting_holes` -- List of mounting hole positions (x, y, diameter)
- `wall_thickness` -- Enclosure wall thickness in mm (default: 2.0)
- `material` -- Material name (default: ABS)

## Output

- `cad_file` -- Path to the generated enclosure STEP file
- `internal_volume` -- Internal volume in mm^3
- `external_dimensions` -- External length, width, height
- `mounting_info` -- Hole count and cutout count
- `material` -- Material used

## Cross-Domain Usage

This is a cross-domain skill: the Electronics Agent (via KiCad) provides PCB
dimensions and connector positions, and the Mechanical Agent generates the
matching enclosure. The Digital Twin mediates the data exchange.

## Limitations

- Generates simple box enclosures with shell (no complex organic shapes)
- Cutouts are rectangular only
- No snap-fit or screw-boss features yet (Phase 3)
- Phase 2 skill
