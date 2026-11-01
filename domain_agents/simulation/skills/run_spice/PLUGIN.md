---
description: Simulate a circuit with ngspice through MetaForge (DC operating point or sweep, AC frequency response, transient), from a KiCad schematic's SPICE netlist or a netlist the user gives, and record the result as evidence. Use when the user asks to simulate a circuit, run SPICE or ngspice, check a filter's cutoff, a regulator's transient response, a bias point or a startup waveform.
---

# run_spice

Get a circuit question answered with evidence a reviewer can trust. MetaForge
runs **ngspice** behind `spice.run_simulation` (FORGE-542): you give it the
circuit and the analysis, it returns the solved vectors. Your job is to make
sure the circuit, the models, the stimulus and the analysis are the user's
and are cited, and to record what came back.

## When to use it

- "Simulate the RC filter and tell me the -3 dB point."
- "What does the 3V3 rail do when the motor starts?" (transient)
- "Check the op-amp's bias point at 25 C." (operating point)
- "Run the buck converter's startup in SPICE."
- A requirement with `verification_method: analysis` names a circuit
  quantity (ripple, cutoff, settling time).

Not for:

- Electrical rule checks on the schematic (`run_erc`).
- Layout rule checks (`kicad.run_drc`).
- A power budget by arithmetic (`check_power_budget`, `power.check_budget`).
- Thermal behaviour of a part (`calculix.run_thermal`).

## Tools and profile

| Step | Tool | Profile |
|---|---|---|
| Check the connection | `health.check` | every profile |
| Get a committed schematic onto disk | `twin.stage_work_product_file` | `simulation`, `mechanical` |
| Electrical sanity first | `kicad.run_erc` | `electronics` |
| Export a SPICE netlist | `kicad.export_netlist` | `electronics` |
| Simulate | `spice.run_simulation` | `simulation`, `electronics` |
| Find device data the user cites | `knowledge.search` | every profile |
| Record question or results | `twin.record_document` | `core` |
| Record modelling choices | `twin.record_decision` | every profile |

`spice.run_simulation` runs in the `spice-adapter` container. If it returns
`-32001` or "ngspice not found", that container is down: say so; do not
estimate the waveform instead.

## Inputs you need before you start

Ask the user for anything missing. Do not pick component values, models or
stimulus yourself.

| Input | Why it matters | Example |
|---|---|---|
| The circuit | A KiCad schematic, a committed schematic work product, or the user's own netlist | power.kicad_sch |
| Analysis type | Decides what is computed | `op`, `dc`, `ac` or `transient` |
| Analysis parameters | The sweep or time window | AC dec 50 points, 10 Hz to 1 MHz; transient 1 us step to 5 ms |
| Stimulus | Sources and their waveforms, in the netlist | "12 V step at t = 0" |
| Device models | A result is only as good as its models | the vendor's model, inline in the netlist |
| The quantity to read | What answers the question | `v(out)` ripple peak to peak |
| The acceptance limit | What counts as a pass | "ripple <= 30 mV" |

## Procedure

### 1. Orient

Call `health.check`. Note the profile, and whether `kicad` or `spice` is in
`unreachable_adapters`.

### 2. Get the netlist

- From a schematic: `kicad.run_erc` first and report error-level violations
  (simulating unconnected pins wastes the run), then `kicad.export_netlist`
  with `output_format: "spice"`. Its `output_file` is on the adapter
  workspace that the SPICE adapter shares, so pass it as `netlist_path`.
- A committed schematic: `twin.stage_work_product_file` with its `node_id`,
  then the same two calls.
- The user's own netlist: pass it as `netlist` text.

Components with no simulation model will not simulate. A model the netlist
`.include`s by a path on the user's machine does not exist on the adapter;
paste the model into the netlist text instead.

### 3. Simulate

Call `spice.run_simulation` with:

- `netlist` or `netlist_path` (exactly one). Leave out any `.control` block.
- `analysis_type`: `op`, `dc`, `ac` or `transient`.
- `params` for the card: dc `source`, `start`, `stop`, `step`; ac
  `variation` (`dec`, `oct`, `lin`), `points`, `fstart`, `fstop`;
  transient `step`, `stop`, optional `start`, `max_step`. Values are SPICE
  numbers (`10u`, `1meg`). Omit `params` only if the netlist already has
  exactly one card of that kind.
- `probes`: the vectors that answer the question, e.g. `["v(out)"]`.

It returns `convergence`, `results` (per vector `final`, `min`, `max` and
`unit`; for AC `mag_db_final`, `mag_db_min`, `mag_db_max`,
`phase_deg_final`), `waveform_data` (decimated, keyed with the `scale`,
e.g. `time` or `frequency`), `waveforms` (the rawfile path on the adapter)
and `sim_time_s`. When `convergence` is false, `log` holds ngspice's errors:
report them; never report values from a run that did not converge.

Read the answer from the data: a -3 dB point is where `mag_db` crosses
3 dB below its low-frequency value in `waveform_data`; quote the two
bracketing points rather than inventing a finer one.

### 4. Record

1. `twin.record_document` with `document_type: "simulation_result"`, a
   `name` naming circuit, analysis and schematic revision, and a `content`
   giving the netlist source, models, analysis card, the result values with
   units, convergence, and "simulated by MetaForge spice.run_simulation
   (ngspice)". Put the key values in `metadata` as top-level keys (for
   example `ripple_mv_pp`) so a constraint can read them.
2. If a component value, a model or the stimulus changed to make the circuit
   pass, record it with `twin.record_decision`, with the original value as
   the alternative.

### 5. Report

- The result against the limit, with the vectors and values it rests on.
- Missing models and any ERC errors still open.
- What was recorded, with node ids; re-read
  `metaforge://twin/requirements/<project_id>` and give the requirement's
  status.

## Checks before you report

- [ ] ERC run on an exported schematic, and error-level violations reported
- [ ] Every stimulus, model and limit came from the user or a cited source
- [ ] `convergence` was true for every value you report
- [ ] Values read from `results`/`waveform_data`, not interpolated by you
- [ ] The recorded result names the netlist, analysis card and models

## Failure handling

| Symptom | Likely cause | What to do |
|---|---|---|
| `-32001`, or "ngspice not found; run the spice-adapter container" | SPICE adapter down | Tell the user; do not estimate. Recording the question still works. |
| `convergence: false`, log "unknown subckt" / "could not find" | A model is missing | Ask for the model; paste it into the netlist. |
| `convergence: false`, "timestep too small" / "singular matrix" | Stimulus or circuit problem (floating node, ideal switch) | Report the log; ask the user how to fix the circuit. |
| "netlist already has analysis card(s)" | Card in the netlist and `params` given | Pass one or the other. |
| "probe(s) not in the results" | Wrong vector name | Use a name from the listed `available` vectors. |
| `kicad.*` missing | Not on the `electronics` profile | Ask for `?profile=electronics`. |
| Write held for approval | Writes need a person | Tell the user where it waits; do not retry. |

## Limits

- ngspice only: no vendor-encrypted (PSpice/LTspice-only) models.
- Waveforms come back decimated (`max_points`, default 200); the full
  rawfile stays on the adapter workspace.
- Non-convergence is reported, not fixed.
- A result is tied to the netlist it was run on; after the schematic
  changes, run it again.
