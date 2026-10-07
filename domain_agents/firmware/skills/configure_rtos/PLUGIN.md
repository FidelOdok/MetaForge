---
description: Write the RTOS configuration for a firmware project (task table, priorities, stack sizes, heap, tick rate) from task definitions and the MCU's memory, check it fits in RAM, and record it in the twin. Use when the user asks to set up or change FreeRTOS, Zephyr, ChibiOS, ThreadX or RTEMS configuration, asks for a task table or stack and heap sizing, or asks whether their tasks fit in the MCU's RAM.
---

# configure_rtos

Produce the RTOS configuration file for a firmware project from the user's
task definitions and the target's memory, with a RAM check a reviewer can
follow, and record what was configured and why.

**No MCP tool generates RTOS configuration.** The server-side skill (used by
the in-process firmware agent, not exposed over MCP) generates FreeRTOS or
Zephyr configuration from task definitions it is given, and records nothing. You write the configuration
yourself, in the user's repository, and record the result in the twin with
real twin tools. Tell the user this plainly.

## When to use it

- "Set up FreeRTOS for the flight controller: IMU at 1 kHz, telemetry,
  logging."
- "What stack sizes and heap do these tasks need, and do they fit?"
- "Move the project from a 1 kHz to a 500 Hz tick."
- "Write the Zephyr prj.conf for these threads."

Not for: the MCU's peripheral layer (`generate_hal`), a device driver
(`scaffold_driver`), proving the task set is schedulable (nothing here does
timing analysis), or a per-joint CAN node table for a robot (if
`twin.create_firmware_scaffold` is in your tool list it derives one from an
assembly's joints; otherwise no tool does).

## Tools and profile

Most of this is your own file editing. The MetaForge tools you use read the
design and record the result. `twin.record_document`, `web.search` and
`web.fetch` are on **`core`**; `kicad.get_pin_mapping` is on
**`electronics`**; a connection with no `?profile=` serves all of them.

| Step | Tool | Profile |
|---|---|---|
| Read requirements and existing firmware records | `metaforge://twin/requirements/<project_id>`, `metaforge://twin/brief/<project_id>`, `twin.get_node` | all |
| MCU part number | BOMItems via `twin.find_by_property` / `twin.get_node` | all |
| MCU RAM size, RTOS documentation | `knowledge.search`; `web.search` then `web.fetch` | all / core |
| Write the configuration | your own file tools, in the user's repo | n/a |
| Record the configuration | `twin.record_document` | core |
| Record design choices | `twin.record_decision` | all |

## Inputs you need before you start

Ask for anything missing. The server-side skill defaults to a 64 KB heap,
a 1000 Hz tick and a 4 KB stack per task; **do not use those defaults**.
They are placeholders, not engineering values.

| Input | Why it matters | Example |
|---|---|---|
| RTOS and its version | Option names and units differ between RTOSes and versions | FreeRTOS 10.6, Zephyr 3.7 |
| MCU (exact part) and its RAM | The configuration must fit | STM32F405RG, 192 KB SRAM incl. 64 KB CCM |
| Each task: name, what it does, rate or trigger, priority | The task table | `imu`, 1 kHz, highest |
| Each task's stack size, and how it was sized | Stack overflow is the commonest RTOS failure | "2 KB, from a high-water-mark measurement" |
| Heap strategy and size | Static vs dynamic allocation changes the whole config | heap_4, 32 KB |
| Tick rate | Sets timing resolution and overhead | 1000 Hz |
| Existing configuration file, if any | Change it, do not replace it | the project's current FreeRTOSConfig.h |
| Where the firmware lives in the repo | Where you write | firmware/src |

If a stack size is not known, say so and propose how to measure it (stack
high-water mark on target) rather than inventing a number.

## Procedure

### 1. Read what exists

1. Read `metaforge://twin/requirements/<project_id>` for firmware
   requirements (loop rates, latency, RAM budget) and
   `metaforge://twin/brief/<project_id>` for earlier firmware records and
   decisions. Earlier decisions bind you unless the user changes them.
2. Find the MCU: the project's BOMItems (`twin.find_by_property` on `mpn`,
   or the brief). If none is recorded, ask.
3. Get the RAM size and any memory regions (for example core-coupled RAM
   that DMA cannot reach) from the MCU datasheet: `knowledge.search`, or
   `web.search` and `web.fetch`. Cite the page.
4. Open the user's existing configuration file in their repo, if any.

### 2. Write the configuration

Write it where the user's build expects it. Conventional names: FreeRTOS
FreeRTOSConfig.h, Zephyr prj.conf (plus thread definitions in source),
ChibiOS chconf.h, ThreadX tx_user.h, RTEMS a configuration header in the
application. Change only what the request covers and keep the user's other
options.

Get the units right. These are the usual traps:

- **FreeRTOS stack depth is in words, not bytes.** `xTaskCreate` takes
  `usStackDepth` in `StackType_t` units (4 bytes on a 32-bit Cortex-M), so a
  2 KB stack is 512. `configTOTAL_HEAP_SIZE` is in bytes.
- **Priority direction differs.** In FreeRTOS a higher number is a higher
  priority, below `configMAX_PRIORITIES`. In Zephyr a lower number is a
  higher priority, and negative priorities are cooperative.
- **Tick rate** is `configTICK_RATE_HZ` in FreeRTOS and
  `CONFIG_SYS_CLOCK_TICKS_PER_SEC` in Zephyr. A task that must run at 1 kHz
  cannot be paced by a 500 Hz tick.

Turn on the RTOS's stack-overflow and malloc-failed checks during
development (for FreeRTOS, `configCHECK_FOR_STACK_OVERFLOW` and
`configUSE_MALLOC_FAILED_HOOK`) unless the user says not to, and say you did.

Check option names against the documentation for the user's RTOS version
before writing them. Do not write an option you have not seen documented.

### 3. Check it fits

Show the arithmetic:

1. Sum of task stacks in bytes (convert from words where needed), plus the
   idle and timer task stacks the RTOS creates itself.
2. Plus the heap.
3. Plus the RTOS's own control blocks, if the user's build reports them.
4. Compare with the RAM available to the application. If the user can build,
   the linker map is the real answer: ask them to build and read RAM usage
   from it rather than trusting your sum.

State whether it fits and by how much. Over budget means stop and ask what to
cut; do not shrink stacks silently.

### 4. Record it

When the user wants it kept, or a flow phase needs it:

1. `twin.record_document` with `document_type: "documentation"`, `name`
   like `RTOS configuration, FC firmware, FreeRTOS`, and `content` with the
   RTOS and version, the repo path of the file you wrote, the task table
   (name, priority, stack in bytes, rate), heap, tick rate, the RAM check and
   its result, and every value marked as measured, from the user, or
   unknown. Documentation is stored as markdown, so put the file's relevant
   section in a fenced code block. Pass `source_part_node_ids` with the
   firmware or schematic work product ids it belongs to, if the project has
   them.
2. `twin.record_decision` for choices a reviewer could question (RTOS
   choice, static vs dynamic allocation, tick rate, priority order), with
   `title`, `rationale` and `alternatives` (`option`, `reason_rejected`).
3. No tool on this server creates a `firmware_source` work product from your
   files. Tell the user the configuration lives in their repository and the
   twin holds a documentation record of it.

### 5. Report

- The file you wrote or changed, and what changed
- The task table, heap and tick rate
- The RAM check, and whether it came from your sum or the linker map
- Unknowns (unmeasured stacks) and how to close them
- What was recorded, with node ids

## Checks before you report

- [ ] RTOS, version, MCU and RAM came from the user or a cited source
- [ ] No placeholder defaults (64 KB heap, 1000 Hz, 4 KB stacks) used unasked
- [ ] Stack units correct for this RTOS
- [ ] Priority direction correct for this RTOS
- [ ] Tick rate supports the fastest periodic task
- [ ] RAM fit shown, with its basis
- [ ] Option names checked against the RTOS documentation
- [ ] User's other configuration left unchanged

## Failure handling

| Symptom | Likely cause | What to do |
|---|---|---|
| No MCU in the twin | BOM not recorded yet | Ask the user for the exact part. |
| Datasheet not readable | Not ingested, or PDF page limit hit | Say which figure is missing; ask the user. |
| Tasks do not fit in RAM | Stacks or heap too large for the part | Show the overrun; ask what to cut. |
| Build fails after your change | Wrong option name or unit for this version | Read the error, check the RTOS docs, fix; do not guess repeatedly. |
| `twin.record_document` not in your list | Connection is not on `core` | Ask for `core` or no profile; report what was not recorded. |
| A record call held for approval | Writes need a person on this connection | Tell the user where it waits; do not retry or reword. |
| `-32001` on a twin or knowledge tool | That service is down | Report it; the file you wrote is still valid. |

## Limits

- Configuration only. No schedulability, worst-case execution time or
  priority-inversion analysis.
- No MetaForge tool compiles firmware or runs it on target. Fit in RAM is
  confirmed only by the user's build.
- The twin records a description of the configuration, not a versioned
  firmware work product.
