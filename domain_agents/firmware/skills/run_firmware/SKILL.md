# run_firmware

Build firmware with PlatformIO and, when checks are given, run it on an emulated board.

## What it does

1. Invokes `firmware.build` in the firmware-adapter container (FORGE-674): PlatformIO compiles the project with the STM32 (STM32Cube) or ESP32 (ESP-IDF) toolchain baked into the image. A project with a `FreeRTOSConfig.h` on an STM32 gets the FreeRTOS kernel compiled in
2. If the build passed and UART or memory checks were given, invokes `firmware.run_emulated` (FORGE-677): Renode for STM32F4-Discovery, Nucleo-F767ZI and Nucleo-H753ZI; Espressif QEMU for the ESP32
3. Returns the compiler's diagnostics, flash and RAM use, and each check's status; it records nothing itself

## Tools Required

- `firmware.build` -- PlatformIO compile, diagnostics, flash/RAM
- `firmware.run_emulated` -- Renode / Espressif QEMU run with UART and memory checks (optional)

## Input

- `work_product_id` -- twin work_product id for the firmware project
- `project_path` (a PlatformIO project on the adapter workspace) or `files` with `board` and `framework`
- `uart_expect`, `uart_forbid`, `memory_expect` -- the checks; none means build only
- `sim_time_s` -- emulated seconds to run (default 2)

## Output

- `build_verdict`, `error_count`, `warning_count`, `diagnostics`, `flash`, `ram`, `elf_path`, `toolchain`
- `emulation_verdict`, `emulator`, `assertions`, `uart_log` when it was emulated

## Limitations

- Installed: STM32Cube for the STM32 F4, F7 and H7 families and ESP-IDF for the ESP32; other boards are refused, other STM32 families are not_established
- Emulation is functional, not cycle-accurate: check what the firmware does, not how long it takes
- A board neither emulator models is `not_established`, never a pass
