# Desk lamp: product definition

## Intent

"Design a modern desktop lamp with adjustable brightness, a compact base, a
curved arm and a soft diffuser. Make it suitable for 3D printing."

A low voltage, USB-C powered, dimmable desktop lamp for reading and desk
work. The goal is a traceable, revision-compatible prototype manufacturing
package: print files, PCB fabrication files, BOM, firmware build and assembly
instructions that all belong to the same revision. It is not a claim of
regulatory certification or a physically manufactured lamp.

## Scope

- Printable base, curved arm and head shell (FDM).
- A diffuser in a translucent material (assumed: translucent PETG).
- An LED module in the head, driven by PWM from a driver/control board in the
  base.
- A USB-C connector on the board, reachable through a cut-out in the base.
- A brightness control: a button or a rotary encoder (not decided yet).
- One functional prototype.

## Out of scope

- Mains voltage. Power is 5 V from USB-C only; electrical and product-safety
  review is still required.
- Certification, packaging and volume production.

## Parts

| Part | Made or bought | Process | Size (estimated) |
|------|----------------|---------|------------------|
| Base | made | FDM | about 120 x 90 x 30 mm |
| Arm (curved) | made | FDM | about 300 mm along the curve |
| Head | made | FDM | about 140 x 60 x 25 mm |
| Diffuser | made | FDM, translucent | about 130 x 50 x 2 mm |
| LED module | bought | | |
| Driver/control PCB | made | PCB fabrication and assembly | about 60 x 40 mm |
| USB-C connector | bought | | |
| Brightness control | bought | | |

Every size is an estimate until the parametric CAD and the PCB layout set it.

## Interfaces that must hold

- The circuit board fits inside the base, its screw holes line up with the
  base's posts, and its USB-C socket lines up with the opening.
- The firmware uses the pins the board assigns to the LED driver and the
  brightness control, within the driver's limits.
- The thermal check has the geometry, materials, LED heat and room
  conditions it needs.
- Every manufacturing file is made from the current revision of what it comes
  from.
