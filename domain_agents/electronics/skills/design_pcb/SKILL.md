# design_pcb

Generate a KiCad schematic, board and routing from a circuit description (Phase 2, KiCad write).

## What it does

1. `kicad.generate_schematic`: library symbols placed and joined by net labels, then KiCad's ERC and the exported netlist compared with the circuit net by net
2. If it passed and the circuit has a `board`: `kicad.generate_pcb` places the library footprints with their nets on the outline
3. If every part fits: `kicad.autoroute_pcb` routes a copy with Freerouting, pours the ground net, then KiCad's DRC with schematic parity

## Tools Required

- `kicad.generate_schematic` -- schematic + ERC + netlist check
- `kicad.generate_pcb` -- footprints, nets, outline, rules, placement
- `kicad.autoroute_pcb` -- Freerouting, pour, DRC with schematic parity

## Input

- `work_product_id`, `circuit` (components with KiCad symbol and footprint lib_ids, nets as REF.PIN, optional board)
- `route` (default true), `passes` (Freerouting passes)

## Output

- Each step's verdict and file; ERC errors and netlist mismatches; unplaced parts; unrouted connections, DRC errors and parity issues; tracks and vias

## Limitations

- Placement is deterministic and connectivity-clustered: a reviewable start, not an optimum
- No impedance control, length matching or high-speed rules
- Symbols and footprints must exist in the installed KiCad libraries
