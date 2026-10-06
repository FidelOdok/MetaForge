---
description: Write a thin hardware abstraction layer for a firmware project's MCU and peripherals (GPIO, SPI, I2C, UART, ADC, PWM, timers, DMA) using the pin assignments from the real KiCad schematic, and record what was generated in the twin. Use when the user asks to generate or set up a HAL, peripheral init code or a pin map for their board, asks to wire firmware to the schematic's pins, or starts firmware for a new board.
---

# generate_hal

Give a firmware project a small, stable interface over its MCU's peripherals,
with every pin taken from the board's actual schematic, so the application
and drivers above it never touch registers or vendor calls directly.

**No MetaForge tool generates HAL code.** The server-side skill only returns
a list of file names and a placeholder pin map (`<family>_DEFAULT` for every
peripheral); it writes no file, reads no schematic and records nothing. Do
not use or repeat its output. You write the HAL yourself in the user's
repository, take the pins from the schematic with a real tool, and record
the result in the twin. Tell the user this plainly.

## When to use it

- "Generate the HAL for the STM32F4 on the flight controller: SPI for the
  IMU, I2C for the baro, UART for GPS."
- "Make a pin map for the firmware from the schematic."
- "Set up peripheral init for the new nRF52 board."

Not for: a driver for one external chip (`scaffold_driver`, which sits on top
of this HAL), RTOS configuration (`configure_rtos`), checking the schematic
itself (`run_erc`), or changing pin assignments: those are schematic changes
the user makes in KiCad.

## Tools and profile

`kicad.get_pin_mapping` is on **`electronics`**; `twin.record_document`,
`web.search` and `web.fetch` are on **`core`**. A connection with no
`?profile=` serves all of them. Staging a schematic out of the twin needs
`twin.stage_work_product_file` (mechanical, simulation, robotics, or no
profile).

| Step | Tool | Profile |
|---|---|---|
| Requirements and earlier firmware records | `metaforge://twin/requirements/<project_id>`, `metaforge://twin/brief/<project_id>` | all |
| MCU part number | BOMItems via `twin.find_by_property` / `twin.get_node` | all |
| Get the schematic to KiCad | `twin.stage_work_product_file` | mechanical, simulation, robotics, no profile |
| Pin and net assignments | `kicad.get_pin_mapping` | electronics |
| MCU reference manual, alternate functions, SDK docs | `knowledge.search`; `web.search` then `web.fetch` | all / core |
| Write the HAL | your own file tools, in the user's repo | n/a |
| Record it | `twin.record_document`, `twin.record_decision` | core / all |

## Inputs you need before you start

Ask for anything missing.

| Input | Why it matters | Example |
|---|---|---|
| The exact MCU | Peripheral instances and alternate functions are per part, not per family | STM32F405RGT6 |
| The vendor SDK or framework the project uses | The HAL wraps it; it does not replace it | STM32Cube HAL / LL, ESP-IDF, nRF Connect SDK, pico-sdk |
| Which peripherals, and what each is for | Decides instance, mode and speed | SPI1 to the IMU at 10 MHz, mode 3 |
| The schematic | The only trustworthy source for pins | twin node, or a path the KiCad adapter can read |
| Clock configuration | Baud rates and SPI speeds derive from it | 168 MHz SYSCLK, APB2 84 MHz |
| Repo layout and existing HAL | Extend, do not replace | firmware/hal |
| Coding conventions | Naming, error handling, C or C++ | "C99, return int error codes" |

## Procedure

### 1. Read what exists

1. Read the requirements and brief resources for firmware requirements and
   earlier pin or HAL decisions.
2. Find the MCU from the project's BOMItems (`twin.find_by_property` on
   `mpn`) or ask.
3. Look at the user's repo: the SDK in use, the existing HAL, the build
   system. Match them.

### 2. Get the pins from the schematic

1. Get a path the KiCad adapter can read: `twin.stage_work_product_file`
   with the schematic's `node_id`, or a path the user gives you inside the
   adapter workspace. A file in the user's local repo is not visible to the
   adapter container.
2. Call `kicad.get_pin_mapping` with `schematic_file` and
   `component_filter` set to the MCU's reference prefix (for example `U`).
   Find the MCU by `reference` and `value`. Each pin comes back with
   `number`, `name` (the pin function, such as `PA5`), `type` and `net`.
3. Build the pin table from it: net name, MCU pin, and the peripheral
   function that net implies (`IMU_SCK` on PA5 is SPI1 SCK). Check each
   against the MCU's alternate-function table in the reference manual or
   datasheet; cite the table.
4. Stop and tell the user when:
   - a net needed for a requested peripheral is not on any MCU pin,
   - a pin's net does not match an alternate function that pin supports,
   - two peripherals need the same pin.
   These are schematic problems. Do not resolve them by picking a different
   pin in firmware.

If KiCad is unreachable, ask the user for the pin table; never fill it from
a reference design or a dev board's defaults.

### 3. Write the HAL

For each requested peripheral, write a header and source pair in the user's
HAL directory (for example hal_spi.h and hal_spi.c) that:

- exposes a small interface the drivers need (init, transfer, read, write),
  not the whole vendor API;
- takes the pin and instance from one generated pin map header, built from
  step 2, so a schematic change touches one file;
- calls the vendor SDK underneath; do not hand-write register access the SDK
  already provides unless the user asks for it;
- returns errors rather than ignoring them.

Keep generated pin definitions and hand-written code apart, so the pin map
can be regenerated from the schematic. Put a comment in the pin map naming
the schematic file and revision it came from.

If the user's toolchain is available locally, build the project and report
whether it compiled. No MetaForge tool compiles firmware.

### 4. Record it

When the user wants it kept, or a flow phase needs it:

1. `twin.record_document` with `document_type: "documentation"`, `name`
   like `HAL and pin map, FC firmware`, `content` with the MCU, SDK, the
   peripheral list with instance, mode and speed, the full pin table (net,
   pin, function, source table), the repo paths you wrote, and whether it
   compiled. Pass `source_part_node_ids` with the schematic's node id so the
   record links to the schematic it was derived from, and `depends_on` with
   the schematic item reference if you have it, so a schematic revision marks
   the record stale.
2. `twin.record_decision` for real choices (which SPI instance, DMA or
   interrupt-driven, LL vs HAL): `title`, `rationale`, `alternatives`.
3. Tell the user no `pinmap` or `firmware_source` work product was created:
   no tool on this server creates one from your files (`twin.create_firmware_scaffold`,
   when listed, only derives a per-joint CAN table from an assembly).

### 5. Report

- Files written and the peripherals they cover
- The pin table, with the schematic revision it came from
- Any schematic conflicts found, left for the user
- Whether it compiled
- What was recorded, with node ids

## Checks before you report

- [ ] Every pin came from `kicad.get_pin_mapping` or from the user
- [ ] Every pin's function checked against the MCU's alternate-function table
- [ ] Conflicts reported, not worked around
- [ ] The HAL wraps the user's SDK and matches their conventions
- [ ] Pin map isolated in one file, naming its schematic source
- [ ] Compile status stated honestly (built, failed, or not attempted)

## Failure handling

| Symptom | Likely cause | What to do |
|---|---|---|
| `-32001` naming `kicad` | KiCad adapter down | Tell the user; ask for the pin table instead. |
| `kicad.get_pin_mapping` returns nothing for the MCU | Wrong `component_filter`, or the file is not readable by the adapter | Retry once without the filter; otherwise stage the file or ask for a path. |
| A pin's `name` is just its `number` | The symbol has no pin function name, so the tool fell back to the number | Use pin `number` with the datasheet's pinout; cite it. |
| Net on a pin that cannot do that function | Schematic error | Report it; do not change the pin in firmware. |
| `kicad.get_pin_mapping` not in your list | Connection not on `electronics` | Ask for `electronics` or no profile. |
| `twin.record_document` not in your list | Connection not on `core` | Ask for `core` or no profile; say what was not recorded. |
| A record call held for approval | Writes need a person on this connection | Tell the user where it waits; do not retry or reword. |

## Limits

- Scaffolding over the vendor SDK, not verified drivers; bring-up on real
  hardware is still required.
- Pin data is only as good as the schematic's symbols and net names.
- The twin holds a description of the HAL and its pin table, not the source
  files themselves.
