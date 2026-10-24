# KiCad project — generated reference board

This is the drone flight controller as MetaForge's Phase 2 electronics pipeline
generates it (FORGE-685). Nothing here was drawn by hand:

| File | What it is |
|------|------------|
| `drone-fc.kicad_sch` | Schematic from `kicad.generate_schematic` |
| `drone-fc.kicad_pcb` | 4-layer board from `kicad.generate_pcb`, routed by `kicad.autoroute_pcb` |
| `drone-fc.kicad_pro`, `sym-lib-table`, `fp-lib-table` | Project and library tables |
| `drone-fc-top.png` | Top render (`kicad.render_pcb`) |
| `report.json` | Every check result from the run that produced these files |

The input is [`../circuit.json`](../circuit.json), written by
[`../build_circuit.py`](../build_circuit.py) from the design by pin *name*
(`U1:PA5`), with each name resolved to the pin number in KiCad's own symbol.
To regenerate, run this in the kicad-adapter image:

```bash
python examples/drone_flight_controller/eda/build_circuit.py
python examples/drone_flight_controller/eda/generate.py
```

## The board

- STM32F405RGT6 (LQFP-64) with an 8 MHz crystal, a SWD header and USB FS
  behind a USBLC6 ESD array on a USB-C receptacle.
- MPU-6000 IMU on SPI1, BMP280 barometer on I2C1, and an AP2112K-3.3 LDO
  from the 5 V input (the ESC's BEC, or USB).
- Motor outputs M1–M4 on PB0/PB1 (TIM3) and PA0/PA1 (TIM5), the pins
  [`firmware/src/pinmap.json`](../../firmware/src/pinmap.json) assigns.
- 40 × 40 mm, 4 layers: In1.Cu a solid GND plane and In2.Cu a +3V3 plane
  (decision DD-002), with GND poured on both outer layers. Tracks are 0.15 mm,
  vias 0.5/0.25 mm, and the 5 V input runs at 0.4 mm.

## What the checks say

KiCad runs every check: ERC on the schematic with 0 errors, the exported
netlist matching the circuit net for net, and the routed board at 0
unconnected items and 0 DRC errors, with schematic parity clean. The STEP
export has a model for all 34 parts. `report.json` records the exact counts.

The placement is the generator's deterministic starting point. It puts the
MCU at the centre, the connectors on the left edge, and each part beside the
one it shares the most with, so the decoupling capacitors line up along the
MCU's supply pins. It is a correct board, not a tuned one. Nothing here
claims signal integrity, controlled impedance for the USB pair, or EMC
performance. Those need a field solver and a person.
