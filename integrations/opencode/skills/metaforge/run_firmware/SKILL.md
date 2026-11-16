---
name: run_firmware
description: Compile firmware with PlatformIO through MetaForge and run it on an emulated board (Renode for STM32, Espressif QEMU for ESP32), checking what it prints on the UART and what it leaves in memory, and record the result as evidence. Use when the user asks whether generated or hand-written firmware builds, how much flash or RAM it uses, whether it boots, or wants it run without hardware.
domain: firmware
---

# run_firmware

Turn "the firmware builds and boots" from a claim into a result. MetaForge
compiles with **PlatformIO** behind `firmware.build` (FORGE-674) and runs
the ELF on **Renode** or **Espressif QEMU** behind `firmware.run_emulated`
(FORGE-677). Your job is to give it the user's sources and the user's
acceptance checks, and to report and record exactly what came back.

## When to use it

- "Does the HAL you generated compile?" (after `generate_hal`,
  `scaffold_driver` or `configure_rtos`)
- "How much flash and RAM does this use on the F407?"
- "Boot it and show me that both tasks start."
- "Run the ESP32 app and check it prints its version."
- A requirement with `verification_method: test` or `analysis` names a
  firmware quantity (flash budget, boot message, task start).

Not for:

- Flashing or debugging a real board (OpenOCD, GDB, ST-Link): needs
  hardware; MetaForge has no tool for it.
- Timing: emulation is functional, not cycle-accurate. Do not quote boot
  time, tick counts or throughput from an emulated run.

## Tools and profile

| Step | Tool | Profile |
|---|---|---|
| Check the connection | `health.check` | every profile |
| Build | `firmware.build` | `electronics` |
| Static analysis | `firmware.static_check` | `electronics` |
| Run on an emulated board | `firmware.run_emulated` | `electronics` |
| Pin map for the HAL | `kicad.get_pin_mapping` | `electronics` |
| Record the result | `twin.record_document` | `core` |
| Record choices | `twin.record_decision` | every profile |

Both tools run in the `firmware-adapter` container, which is opt-in (the
`firmware` compose profile). If a call returns `-32001` or "pio not found;
run the firmware-adapter container", the container is not running: say so;
never say the firmware builds without a build.

## Inputs you need before you start

Ask for anything missing. Do not choose the board, the framework or the
pass criteria yourself.

| Input | Why it matters | Example |
|---|---|---|
| The sources | What is built | the files `generate_hal` and `configure_rtos` returned, plus the user's `main.c` |
| The board | Selects the part, its memory and the emulator | `disco_f407vg`, `nucleo_f767zi`, `nucleo_h753zi`, `esp32dev` |
| The framework | Which SDK the sources use | `stm32cube` (STM32), `espidf` (ESP32) |
| The checks | What counts as working | UART shows `task sensor started`; never `HardFault` |
| Limits | What counts as fitting | "flash under 50 %" |

## Procedure

### 1. Orient

Call `health.check`. If `firmware` is in `unreachable_adapters`, stop and
tell the user the firmware adapter is not running.

### 2. Build

Call `firmware.build` with either:

- `files`: `[{path, content}]` (sources under `src/`, headers wherever the
  sources include them from), plus `board` and `framework`, and
  `build_flags` for include paths, e.g. `["-Isrc/board", "-Isrc/rtos"]`;
- or `project_path`: a PlatformIO project already on the adapter workspace.

The sources are copied before the build; nothing of the user's is changed.
On an STM32, a `FreeRTOSConfig.h` anywhere in the sources makes the adapter
compile the FreeRTOS kernel in (and, for Cortex-M4F/M7 ports, the FPU
flags the port needs); `freertos` in the result says what was added. On
ESP-IDF, FreeRTOS is part of the framework.

It returns `verdict`, `error_count`, `warning_count`, `diagnostics` (file,
line, column, severity, message, from the compiler and linker), `flash` and
`ram` (`used_bytes`, `total_bytes`, `used_pct`), `toolchain` (platform and
package versions) and `elf_path`. On failure, `log_tail` holds the end of
the build log. Report errors at their file and line, as the compiler said
them; do not paraphrase them into something else.

### 3. Static analysis (when asked, or before a review)

Call `firmware.static_check` with the same `files`/`project_path`, `board`
and `framework`. It runs `pio check` with cppcheck (add `tools:
["cppcheck", "clangtidy"]` for clang-tidy too) on the project's sources and
returns `findings` (tool, severity, rule `id`, CWE, file:line, message),
`counts` per severity, and `verdict`: `fail` when a finding is at or above
`fail_on` (default `high`), so style findings alone do not fail it.

Report high findings at their file and line with the rule id. Never
suppress a finding to make the check pass: a suppression needs the user's
reason (`suppressions: [{id, file?, reason}]`), it is echoed in the result,
and it belongs in the recorded result too.

### 4. Run it (when there is something to check)

Call `firmware.run_emulated` with the `elf_path` and the same `board`, and
the user's checks:

- `uart_expect`: regexes that must appear, in this order;
- `uart_forbid`: regexes that must not appear (`HardFault`, `assert`,
  `Guru Meditation`);
- `memory_expect` (Renode only): `{address, value, mask}` words read after
  the run, e.g. a GPIO output register;
- `sim_time_s`: emulated seconds (default 2).

Renode runs `disco_f407vg` (UART: USART2), `nucleo_f767zi` and
`nucleo_h753zi` (USART3); Espressif QEMU runs the ESP32 (UART0), e.g.
`esp32dev`. Any other board comes back `not_established` with the list of
modelled boards: say that the firmware built but was not run. Never call a
`not_established` run a pass.

### 5. Record

1. `twin.record_document` with `document_type: "simulation_result"`, a
   `name` naming the firmware, board and commit or revision, and a
   `content` giving the build verdict, the diagnostics, flash and RAM, the
   toolchain versions, and for an emulated run the emulator and its
   version, each check with its status and the UART log excerpt that
   decides it. Say "built by MetaForge firmware.build (PlatformIO)" and
   "run on <emulator> by firmware.run_emulated". Put `flash_used_pct`,
   `ram_used_pct`, `build_error_count` in `metadata` as top-level keys so a
   constraint can read them.
2. If a flag, a heap size or a stack size was changed to make it build or
   fit, record it with `twin.record_decision`, with the original value as
   the alternative.

### 6. Report

- Build: verdict, errors at file:line, flash and RAM against the part.
- Run: emulator, each check pass/fail/not_established, the UART lines.
- What was recorded, with node ids.

## Checks before you report

- [ ] Every "it builds" rests on a `firmware.build` with verdict `pass`
- [ ] Every "it boots" rests on a `firmware.run_emulated` with verdict `pass`
- [ ] Errors quoted at the compiler's file and line
- [ ] Every suppressed static-check rule carries the user's reason
- [ ] No timing claim drawn from an emulated run
- [ ] The recorded result names the board, toolchain and emulator versions

## Failure handling

| Symptom | Likely cause | What to do |
|---|---|---|
| `-32001`, or "pio not found; run the firmware-adapter container" | Firmware adapter not running (`firmware` profile) | Tell the user; do not claim a build. |
| "board X is on platform Y; this adapter has ststm32, espressif32 installed" | Board's toolchain not in the image | Say so; ask whether a supported board will do. |
| `verdict: fail`, `region 'RAM' overflowed by N bytes` | Does not fit | Report N; suggest the user's heap/stack figures as the lever, do not change them silently. |
| `verdict: fail`, `undefined reference to ...` | A source or library is missing | Report the symbol; ask for the source. |
| `verdict: not_established`, "build exceeded ...s" | Build timed out | Report it; a larger `FIRMWARE_MAX_BUILD_TIME` is the operator's call. |
| `verdict: not_established`, "the build needs platformio/framework-stm32cube..." | That STM32 family's framework is not in the image (it carries F4, F7, H7) | Say the build was not run and which package it needed; do not call it a failure of the code. |
| Run `not_established`, "no emulator model for board" | Board not modelled | Report the build; say it was not run. |
| Run `fail` on `uart_expect` | The firmware did not print it (or not in order) | Show the UART log; do not loosen the check. |
| Write held for approval | Writes need a person | Tell the user where it waits; do not retry. |

## Limits

- Platforms: ST STM32 with STM32Cube for the F4, F7 and H7 families, and the
  Espressif ESP32 with ESP-IDF.
- Emulated: the three STM32 boards and the ESP32 above; peripherals beyond
  UART, GPIO and timers
  are modelled only as far as Renode / QEMU model them. ESP32-S3 and C3 are
  not run yet.
- Emulation is functional, not cycle-accurate.
- A result is tied to the sources it was built from; after they change,
  build again.
