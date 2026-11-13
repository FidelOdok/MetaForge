# Product context

Every new product gets a `context/` folder next to `product.json`, and the config says `"context": "context"`. The context is the product's environment: the real parts, the machines that can make it, every option the user can choose, the models that simulate it, and the scenario tests. It is also the starting point handed to cloud design, so record everything with a source.

| File | Holds |
|---|---|
| `components.json` | Real parts with sources, interfaces (for wiring) and geometry (for 3D) |
| `machines.json` | Machines and suppliers for this product, on top of the user's registry and the defaults |
| `options.json` | Every decision, its candidates, the default pick and why |
| `models.json` | Formulas for tier 1 and tier 2 simulation (see `models.md`) |
| `scenarios.json` | Scenario tests the user asked for (see `scenarios.md`) |

`scripts/build_page.py` validates the context, applies the default picks, and embeds the whole context so the page can switch options live.

## Components: compulsory and real

For every function, the board, the battery and any other bought part, find **at least two real candidates** with web search. Read the manufacturer datasheet or product page, not a reseller summary, when you can. Record, for each part:

```json
{
  "id": "gnss-a", "role": "fn:e_gnss",
  "name": "GNSS module, multi-band", "mpn": "<exact order code>", "manufacturer": "<maker>",
  "datasheet": "https://...", "retrieved": "2026-11-06",
  "cost": { "unit": 9.80, "qty": 250, "currency": "GBP", "source": "https://distributor/..." },
  "mass": 3.2,
  "specs": { "cur": { "sleep": 0.015, "locate": 25 }, "fix": 30 },
  "interfaces": [
    { "id": "vcc", "bus": "pwr", "dir": "in", "v": [3.0, 3.6], "imax_mA": 40, "at": [-6, 0, 1] },
    { "id": "uart", "bus": "uart", "dir": "bi", "level_V": 3.3,
      "connector": { "family": "JST SH", "pins": 6, "pitch": 1.0, "mate": "SHR-06V-S" }, "at": [-6, 2, 1] }
  ],
  "geometry": {
    "envelope": [18, 18, 6], "source": "datasheet mechanical drawing, page 12", "place": "ceil",
    "body": [ { "shape": "box", "at": [0, 0, 3], "size": [18, 18, 6], "color": "#2b2f36" },
              { "shape": "box", "at": [0, 0, 6.5], "size": [15, 15, 1], "color": "#c9b26b" } ],
    "holes": [],
    "access": [],
    "keepouts": [ { "kind": "sky", "face": "+z", "depth": 20 } ],
    "model": "https://maker/.../part.step"
  }
}
```

Rules:
- `role` is `fn:<function id>`, `board`, `battery`, `fixing`, `material` or `other`.
- `mpn`, `manufacturer`, `datasheet` (a link) and `retrieved` (the date you read it) are required. The builder fails without them.
- Prices and stock are only as fresh as `retrieved`. Say so in the hand-over.
- **Excluded parts:** when the user excludes a maker or a part, keep the entry with `"excluded": true, "standIn": true` and typical specs. The page flags stand-ins.
- Never invent a part number. If you cannot find a real part, say so and add a stand-in.
- Docs and summaries stay neutral about makers (see the project rules); the part data itself keeps the real maker.

### Interfaces: the wiring comes from them

Every electrical part lists its interfaces. The page turns them into typed ports, wires compatible ones, and fails mismatched voltage windows or logic levels with a fix (a regulator or level shifter).

- `bus`: one of pwr, i2c, uart, spi, gpio, usb, rf, pwm, can, analog
- `dir`: in, out or bi. A power output (`pwr`, `out`) is a rail other parts can draw from.
- `v` (volts, [min, max]) is required for power. `imax_mA` for power in and out. `level_V` for logic.
- `connector`: family, pins, pitch, and the mating housing as `mate`. These become harness BOM lines.
- `at`: where the interface sits on the part, in mm from the part centre. Harness length is routed between these points.

### Boards and power from another system

- A board's `specs.elec` gives `vin` [min, max], `vnom` (the voltage it actually runs at, for example 7.4 for a 2S pack), `sleep` and `active` in mA, and its pin counts (`gpio`, `uart`, `spi`). Without `vnom`, heat is worked out at the middle of `vin`, which is wrong for wide-input boards.
- A board's non-power interfaces are offered to peripherals by bus, so give it a `gpio` interface if spare pins drive LEDs or read buttons, and say so in a note.
- A product powered by another system (an aircraft's flight pack, a vehicle, a host) lists the `e_ext` function and sets the voltage window with `catalogue.blocks.e_ext.out.vext`. With no battery, a 3.3 V regulator is added automatically for 3.3 V parts.
- A board with no mounting holes (a module on a carrier) leaves `holes` out; the page then draws no standoffs and lists no board screws.

### Geometry: the 3D design comes from it

Every picked part must have geometry before the page builds: the CAD is only complete when every part is designed. A candidate without geometry can stay in the list for comparison, but it cannot be the pick.

The page packs the real parts and, on first load, grows the form until every part, board and fixing fits; the user can change it afterwards. Take geometry from the datasheet mechanical drawing:

- `envelope` [L, W, H] in mm: required when geometry is given.
- `body`: boxes and cylinders that look like the part (`at` is the centre, `size` is [x, y, z]; a cylinder's size is [d, d, h]). Keep it to a few shapes.
- `holes`: mounting holes as [x, y, diameter] from the part centre. Bosses go where these are.
- `access`: faces that must reach the outside, with `kind` (usb, button, led, sound, sensor, antenna, sky, connector, vent, cable), `face` (+x, -x, +y, -y, +z, -z), `at` [u, v] on that face and `size` [w, h]. Openings are cut here.
- `keepouts`: space that must stay clear, with `kind` (antenna, sky, airflow, heat, magnetic, clearance), `face` and `depth`.
- `place`: floor, ceil, wall or any.
- `model`: a link to the maker's STEP or 3D file when one exists. It is passed on to cloud design; the page does not load it.
- `source`: the drawing it came from.

## Machines: everything that can make it

The builder merges, in order:
1. `context/machines.json`: machines for this product, including suppliers you found by web search
2. `~/.formgraph/machines.json`: the user's own registry (their kit and named suppliers)
3. `data/suppliers.uk.json`: built-in real UK suppliers, for example:
   - SLS and MJF: 3DPRINTUK, 3D People, Protolabs
   - moulding: Protolabs, Rutland Plastics
   - CNC: Protolabs, Geomiq
   - carbon tube: Easy Composites, Carbon Fibre Tubes Ltd
   - carbon plate: Carbon Technology, The Arc Angels
   - sheet metal: Andover Laser, Lasered
   - FDM: IPFL, Hark Tech

   Each has its location, the machines it uses where known, materials, limits, build size, lead time, price basis and source.
4. `data/machines.default.json`: typical capabilities for SLS, MJF, FDM, CNC 3 and 5 axis, moulding, carbon tube and plate, sheet metal and PCBA

Earlier entries win on the same id. Search for at least two real suppliers for the chosen process and record their published design rules:

```json
{ "id": "supplier-x-sls", "name": "<supplier> SLS service", "process": "sls", "source": "https://supplier/design-guide",
  "buildVolume": [340, 340, 600], "materials": ["pa12"],
  "limits": { "wallMin": 0.8, "holeMin": 1.5, "slotMin": 0.6, "featureMin": 0.6, "tolerance": 0.3 },
  "finishes": ["dyed"], "leadDays": 7, "qtyRange": [1, 5000] }
```

A process the engine does not know (for example `mjf`) needs a `processes` entry with a `ruleBase` of im, cnc, sls or fdm, and its materials under `materials`. `fdm` is built in (MetaForge): desktop FDM on a Bambu Lab P1S-class printer with its own rule book (whole perimeters, the closed top as a bridge, warping, screws in printed plastic), never the SLS rules. A library material named for its family, such as `prusament_petg` or `bambu_petg_cf`, screens as `petg` or `petgcf`.

## Options: everything the user can choose

```json
{ "decisions": [
  { "id": "gnss", "label": "GNSS receiver", "kind": "part", "candidates": ["gnss-a", "gnss-b"], "pick": "gnss-a", "why": "lower current at the same accuracy" },
  { "id": "machine", "label": "Who makes the shell", "kind": "machine", "candidates": ["my-sls", "supplier-x-sls", "mjf-generic"], "pick": "my-sls", "why": "in house, 2 day turnaround" },
  { "id": "material", "kind": "material", "candidates": ["pa12", "pa12gb"], "pick": "pa12", "why": "tougher in a fall" },
  { "id": "fixing", "kind": "fixing", "candidates": ["pt25", "ins25"], "pick": "ins25", "why": "the lid is opened for service" }
] }
```

Every function, the board, the battery, the machine, the material and the fixing gets a decision. Use `variant` decisions for choices that are model inputs rather than parts (battery pack, prop size), and `sets` on a part to feed its published values into the models (read `references/airframe.md`). Default picks are applied when the page builds, and the user switches live on the page.

## Made parts: each with its own process and supplier

Parts you make, other than the fuselage shell, go in `context/manufacturing.json`. Each one can be made a different way. The page checks each candidate supplier (size, wall, material, volume range), estimates the cost and mass from the part's real size, and makes the picked one a BOM line with its joint fixings. The assembly check counts every made part, joint, fastener and supplier.

```json
{ "schemaVersion": 1, "parts": [
  { "id": "arm", "name": "Arm tube", "qty": 4, "shape": "tube", "from": "airframe.arm", "wall": 1.2, "pick": "cfrp-tube-generic",
    "candidates": [ { "machine": "cfrp-tube-generic", "material": "cfrp" }, { "machine": "cnc3-generic", "material": "al6061" } ],
    "join": { "method": "bond" } },
  { "id": "motor-mount", "name": "Motor mount", "qty": 4, "shape": "plate", "from": "airframe.motor_mount", "thickness": 3, "pick": "cfrp-plate-generic",
    "candidates": [ { "machine": "cfrp-plate-generic", "material": "cfrp" }, { "machine": "cnc3-generic", "material": "al6061" } ],
    "join": { "method": "screw", "fastener": "M3 x 6", "count": 4 } }
] }
```

- `shape`: tube, plate or block. `wall` for tubes and blocks, `thickness` for plates.
- `from` takes the size from the generated airframe:
  - `airframe.arm`, `airframe.hinge`, `airframe.leg`
  - `airframe.motor_mount`, `airframe.battery_tray`, `airframe.gimbal_mount`

  Use `dims` [L, W, H] in mm for anything else.
- `candidates`: at least two machine and material pairs, from different processes or suppliers where possible.
- `join.method`: screw, bolt, rivet, snap, bond, clamp, press or none. Name the fastener and give the count per part for screws, bolts and rivets.
- **Made-part processes:** `cfrp-tube`, `cfrp-plate` and `sheet` (in the default machines) make small parts only and are never offered for the fuselage shell.
- **Machines added later:** a machine added on the page, or by `/formgraph:context`, is offered to every made part that already has a candidate of the same process.
