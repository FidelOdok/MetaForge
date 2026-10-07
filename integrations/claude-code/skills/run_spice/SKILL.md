---
name: run_spice
description: Handle a circuit simulation request (DC operating point or sweep, AC frequency response, transient) honestly. MetaForge currently has no SPICE simulator on its MCP server, so this skill prepares a SPICE netlist from the KiCad schematic, records the analysis question and its assumptions, and records results the user runs in their own simulator. Use when the user asks to simulate a circuit, run SPICE, ngspice or LTspice, check a filter's cutoff, a regulator's transient response, a bias point or a startup waveform.
domain: simulation
---

# run_spice

Get a circuit question answered with evidence a reviewer can trust, without
pretending MetaForge ran a simulation it cannot run. The skill's harness
handler expects a spice.run_simulation tool, but **no SPICE tool is
registered on the MCP server**: the SPICE adapter in the code base is an
empty placeholder. What you can do over MCP is export the schematic as a
SPICE netlist, check the schematic electrically, record exactly what the
simulation must answer, and record the results when the user runs it in
their own simulator.

## When to use it

- "Simulate the RC filter and tell me the -3 dB point."
- "What does the 3V3 rail do when the motor starts?" (transient)
- "Check the op-amp's bias point at 25 C." (DC operating point)
- "Run the buck converter's startup in SPICE."
- A requirement with `verification_method: analysis` names a circuit
  quantity (ripple, cutoff, settling time).

Not for:

- Electrical rule checks on the schematic (use `kicad.run_erc` via the
  `run_erc` skill).
- Layout rule checks (`kicad.run_drc`).
- A power budget by arithmetic over the BOM (use the `check_power_budget` skill; it
  is not a simulation).
- Thermal behaviour of a component on a part (use the `run_cfd` skill's
  conduction path or `calculix.run_thermal`).

## Tools and profile

| Step | Tool | Profile |
|---|---|---|
| Check the connection | `health.check` | every profile |
| Get a committed schematic onto disk | `twin.stage_work_product_file` | `simulation`, `mechanical` |
| Electrical sanity first | `kicad.run_erc` | `electronics` |
| Export a SPICE netlist | `kicad.export_netlist` | `electronics` |
| Find device data the user cites | `knowledge.search` | every profile |
| Record question or results | `twin.record_document` | `core` |
| Record modelling choices | `twin.record_decision` | every profile |

There is no spice tool of any kind on the server. If a SPICE simulation tool does
appear in your tool list in future, read its own description and arguments
before using it; do not assume the argument names the harness handler uses.
Until then, do not describe any output in this skill as a MetaForge
simulation result.

## Inputs you need before you start

Ask the user for anything missing. Do not pick component values, models or
stimulus yourself.

| Input | Why it matters | Example |
|---|---|---|
| The circuit | A KiCad schematic path, a committed schematic work product, or the user's own netlist file | power.kicad_sch |
| Analysis type | Decides what is computed | `dc`, `ac` or `transient` |
| Analysis parameters | The sweep or time window | AC 10 Hz to 1 MHz, 50 points per decade; transient 1 us step to 5 ms |
| Stimulus | Sources and their waveforms | "12 V step at t = 0, 2 A load step at 1 ms" |
| Device models | A SPICE result is only as good as its models | the vendor's model file for the regulator |
| The quantity to read | What answers the question | "V(out) ripple peak to peak" |
| The acceptance limit | What counts as a pass | "ripple <= 30 mV" |
| Where each number came from | A stimulus or load with no source is not evidence | the requirement, a datasheet, a measurement |

## Procedure

### 1. Orient

1. Call `health.check`. Note the active profile (`active` under `profile`), and whether `kicad` is in
   `unreachable_adapters`.
2. Tell the user up front: MetaForge can prepare and record this simulation,
   but the solve itself has to run in their simulator (ngspice, LTspice,
   or similar). Ask whether they want to continue on that basis.

### 2. Get the schematic

- If the user gave a schematic path the KiCad adapter can read, use it.
- If the schematic is a committed work product, call
  `twin.stage_work_product_file` with its `node_id` and use the returned
  `file_path`.
- If the user already has a netlist of their own, skip to step 4; you do not
  need to export one.

### 3. Check, then export

1. Call `kicad.run_erc` with `schematic_file`. Report any error-level
   violations before going further: simulating a schematic with unconnected
   pins or conflicting drivers wastes the user's time.
2. Call `kicad.export_netlist` with `schematic_file` and
   `output_format: "spice"`. Read `output_file`, `total_nets`,
   `total_components` and `format`.
3. Tell the user where the netlist was written. The path is inside the
   KiCad adapter's workspace, which may not be reachable from their machine;
   if it is not, they can export the same SPICE netlist from KiCad's own
   simulator export.
4. Check `total_components` against what the user expects. Components with
   no simulation model in the schematic will not simulate; list any the user
   must supply models for.

### 4. Record the analysis question

Call `twin.record_document` with `document_type: "documentation"`, a `name`
such as `3V3 rail load-step transient, analysis definition`, `project_id`,
and a `content` that states:

- the question and the quantity that answers it
- the analysis type and parameters
- the stimulus and loads, each with its source
- the device models required, and which the user has
- the acceptance limit
- that the simulation is to be run outside MetaForge

This lets a reviewer see what was asked even before a result exists.

### 5. Record the user's results

When the user runs the simulation and gives you the results:

1. Read back what they report. Do not interpolate, smooth or extrapolate
   numbers they did not give.
2. Call `twin.record_document` with `document_type: "simulation_result"`, a
   `name` naming circuit, analysis and schematic revision, `format: "json"`
   if you pass structured values, and a `content` that names the simulator
   and version, the models used, the analysis settings, the result values
   with units, whether the run converged, and the sentence "simulated
   outside MetaForge and reported by the user". Put the key values in
   `metadata` as top-level keys (for example `ripple_mv_pp`) so a
   constraint can read them.
3. If the user changed a component value, a model or a stimulus to make the
   circuit pass, record that with `twin.record_decision`, including the
   original value as the alternative.

### 6. Report

- What MetaForge did (ERC, netlist export, records) and did not do (the
  solve).
- The result against the limit, as the user reported it, or "not yet
  simulated".
- Missing models and any ERC errors still open.
- What was recorded, with node ids; re-read
  `metaforge://twin/requirements/<project_id>` and give the requirement's
  status.

## Checks before you report

- [ ] The user knows the solve did not run in MetaForge
- [ ] ERC run, and error-level violations reported
- [ ] Every stimulus, load and limit came from the user or a cited source
- [ ] Every device that needs a model has one, or is listed as missing
- [ ] Recorded results name the external simulator and say who reported them
- [ ] No waveform value produced by you

## Failure handling

| Symptom | Likely cause | What to do |
|---|---|---|
| `-32001` naming `kicad` | KiCad adapter container down | Tell the user; do not loop. Recording the question still works. |
| `kicad.*` tools missing | Not on the `electronics` profile | Ask the user to connect with `?profile=electronics`. |
| `twin.record_document` missing | Not on the `core` profile | Record on a `core` connection. |
| "schematic_file is required" or file not found | A twin id or a path the adapter cannot see | Stage it with `twin.stage_work_product_file`, or ask for a path. |
| ERC reports errors | Real schematic problems | Report them; ask whether to proceed anyway. |
| User reports non-convergence | Model, stimulus or timestep problem in their run | Record it as not converged; do not report waveform values from it. |
| Write held for approval | Writes need a person on this connection | Tell the user where it waits; do not retry. |
| You were asked to "just estimate" the waveform | No solver exists | Offer a labelled hand calculation with formula and inputs; never record it as a simulation result. |

## Limits

- No circuit is solved by MetaForge today; every number in a SPICE result
  comes from the user's own simulator.
- The exported netlist is only as complete as the schematic's simulation
  fields; vendor models are not fetched for you.
- Non-convergence is reported, not fixed.
- A result is tied to the schematic revision it was run on; after the
  schematic changes, it must be re-run.
