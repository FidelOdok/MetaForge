---
name: design_pcb
description: Generate a KiCad 9 schematic, PCB and routing through MetaForge from the circuit the user and you have worked out (parts as KiCad symbols and footprints, nets as REF.PIN), each step checked by KiCad's own ERC, DRC and schematic parity, and record the result. Use when the user asks for a schematic, a board, a layout or routing to be made, not checked.
domain: electronics
---

# design_pcb

Turn a circuit into a real KiCad project. MetaForge generates the schematic
(`kicad.generate_schematic`), the board (`kicad.generate_pcb`) and the
routing (`kicad.autoroute_pcb`, Freerouting) from one circuit description
(FORGE-683). KiCad judges every step: its ERC and exported netlist for the
schematic, its DRC with schematic parity for the routed board. Your job is
the circuit itself, cited part by part, and an honest report of what KiCad
said.

## When to use it

- "Draw the schematic for the 3.3 V regulator and the LED."
- "Lay out the board, 40 x 30 mm, two layers, and route it."
- "Make the PCB for the circuit we agreed."

Not for:

- Checking a schematic or board the user made (`run_erc`, `run_drc`).
- Changing values on an existing design: `kicad.update_components`, then
  regenerate the board if a footprint changed.
- Impedance-controlled, length-matched or high-speed layout: the router
  knows clearances and widths, nothing more.

## Tools and profile

| Step | Tool | Profile |
|---|---|---|
| Find parts | `component.search_parametric`, `knowledge.search` | electronics |
| Schematic | `kicad.generate_schematic` | pcb_design |
| Board | `kicad.generate_pcb` | pcb_design |
| Routing | `kicad.autoroute_pcb` | pcb_design |
| 3D model, renders | `kicad.export_3d`, `kicad.render_pcb` | pcb_design |
| Change a part | `kicad.update_components` | pcb_design |
| Fabrication files | `kicad.export_gerber`, `kicad.export_drill`, `kicad.export_pos` | pcb_design, electronics |
| Record | `twin.record_document`, `twin.record_decision` | core |

The tools run in the `kicad-adapter` container. `-32001` means it is down:
say so; never describe a schematic or board you did not get back.

## The circuit

Ask for anything missing; do not choose parts, values or the board yourself.

```json
{
  "name": "ldo_led",
  "components": [
    {"ref": "U1", "symbol": "Regulator_Linear:AMS1117-3.3",
     "footprint": "Package_TO_SOT_SMD:SOT-223-3_TabPin2", "value": "AMS1117-3.3",
     "fields": {"MPN": "AMS1117-3.3"}}
  ],
  "nets": [{"name": "+3V3", "nodes": ["U1.2", "C2.1"]}],
  "board": {"width_mm": 30, "height_mm": 22, "layers": 2, "ground_pour": "GND",
            "power_nets": ["VIN", "+3V3", "GND"], "power_track_mm": 0.5,
            "mounting_holes_mm": 3.2}
}
```

- `symbol` and `footprint` are KiCad library ids (`Library:Name`) that
  exist in KiCad's own libraries; pick the footprint that matches the
  ordered part's package (its datasheet), not one that looks close.
- Nodes are `REF.PIN` with the **symbol's pin number** (`U1.2`, not
  `U1.VO`). Check pin numbers against the datasheet: symbol and part must
  agree.
- Every pin on no net gets a no-connect flag; say which pins those are.
- A board with a fine-pitch MCU (LQFP, QFN) wants four layers:
  `"layers": 4, "inner_planes": ["GND", "+3V3"]` puts solid planes on In1
  and In2, and the router reaches them through vias it is given, not tracks.
  Leave plane nets out of `power_nets`: a wide power track cannot leave a
  0.5 mm-pitch pin. Take track, via and `hole_clearance_mm` from the fab's
  limits, and say which fab.
- A component may name its own 3D `model` when KiCad's footprint points at a
  model the library does not ship; say so in the report.

## Procedure

1. **Schematic.** Call `kicad.generate_schematic` with the circuit. It
   returns `verdict`, `erc` (errors, warnings, violations), `netlist_check`
   (nets matched and mismatched), `power_flags`, `no_connects` and the
   schematic file. Report any ERC error at its location; a mismatch means
   the schematic does not say what the circuit says: stop and fix the
   circuit.
2. **Board.** With a `board`, call `kicad.generate_pcb` with the circuit and
   the `schematic_file`. Footprints are linked to their symbols. `unplaced`
   lists parts that did not fit: ask for a bigger board or fewer parts;
   never shrink clearances to make them fit.
3. **Routing.** Call `kicad.autoroute_pcb` with the `pcb_file` and the
   ground net as `ground_pour`. It routes a copy and returns `unrouted`,
   `drc` (errors, warnings), `schematic_parity`, tracks and vias. `pass`
   means all three checks are zero. Report unrouted nets by name.
4. **Fabrication and 3D.** On a pass, export Gerbers, drill and positions
   from the routed board; `kicad.export_3d` (`step` for the enclosure
   designer, `glb` for viewing) and say which parts have no 3D model
   (`missing_models`); `kicad.render_pcb` with `view: iso` and `inline: true`
   to show the person the board.
5. **Record.** `twin.record_document` with the files, the circuit, the
   KiCad verdicts and their counts; `twin.record_decision` for any part,
   footprint or board-size choice made on the way.

## Checks before you report

- [ ] Every part's symbol, footprint and pin numbers come from its datasheet
- [ ] "Schematic done" rests on ERC with 0 errors and 0 netlist mismatches
- [ ] "Board done" rests on 0 unrouted, 0 DRC errors, 0 parity issues
- [ ] DRC warnings listed, not hidden
- [ ] Placement described as a starting point for review

## Failure handling

| Symptom | Likely cause | What to do |
|---|---|---|
| "symbol X is not in the Y library" / "footprint ... not in" | Wrong lib id | Search the library names; ask if unsure. |
| "U1.9: ... has no pin '9'" | Pin number does not exist on the symbol | Check the symbol's pins against the datasheet. |
| ERC errors | Unconnected input, conflicting outputs, power not driven | Fix the circuit and regenerate. |
| `unplaced` not empty | Board too small | Ask for a larger board. |
| `unrouted` > 0 | Too dense for two layers, or supply pins with no plane | Four layers with `inner_planes`, a larger board, or smaller passives (0402); report the nets by name. |
| DRC `hole_clearance` inside one footprint | The vendor's land pattern sits closer to its holes than KiCad's 0.25 mm default | Set `hole_clearance_mm` to what the fab allows, or choose another footprint; never hide the error. |
| `schematic_parity` issues | Board and schematic disagree | Regenerate the board from the schematic. |
| `-32001` | kicad adapter down | Tell the user. |

## Limits

- Placement is deterministic and connectivity-clustered (connectors on the
  left edge), not optimal; a person reviews it.
- Routing honours clearance, track widths, net classes and vias, nothing
  more: no impedance, length matching or differential pairs.
- Only parts in KiCad's installed libraries.
