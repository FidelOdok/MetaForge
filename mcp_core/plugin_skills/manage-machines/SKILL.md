---
name: manage-machines
description: Record and read the machines a person can make parts on (3D printers, CNC mills and routers, laser cutters, press brakes, vendor machines) in the MetaForge machine library, with a source for every capability, and turn a machine into limits for process ranking and DFM. Use when the user mentions a machine they own or use ("I have a CR-10", "our shop has a 3-axis mill"), asks what they can make in-house, or when a manufacturing phase needs real machine limits. Do not use to start or control a print or cut.
---

# Manage machines

The machine library holds what can actually make a part, once, for every
project. Process ranking and the DFM review read it, so a design is held to
the machine that will make it rather than to a guess.

Two layers:

- A **model** is the catalogue entry for a machine type ("Creality CR-10"):
  build volume, nozzle, materials, each from the manufacturer's spec sheet.
- An **instance** is one real machine ("Workshop CR-10"): it points at a
  model, overrides what differs (a 0.6 mm nozzle, a measured minimum wall),
  and carries its own status (available, busy, offline, retired), location
  and route (`in_house`, or `vendor` for a supplier's machine).

## Rules

1. **Every capability has a source.** `manufacturer_spec`, `measurement` and
   `machine_report` (read from the controller) need a reference (the spec sheet URL, the calibration print and date).
   `user` means the person told you. `assumption` is allowed only when the
   person accepts it, and it is flagged for review in every limit that uses
   it. Never fill a figure from memory: leave it out, and the DFM check reads
   that limit as unknown.
2. **Writes are held.** `library.register_machine` and
   `library.update_machine` wait for the person's approval. Say so and stop;
   do not retry.
3. **No secrets.** A `connection` names the protocol, the URL and
   `credential_env`, the name of an environment variable on the gateway
   (it must start `METAFORGE_MACHINE_`, for example
   `METAFORGE_MACHINE_CR10_KEY`; the person sets it on the gateway) that
   holds the key. Never put an API key or password in the library.
4. **Look before adding.** Call `library.list_machines` first. Reuse an
   existing model rather than adding a second CR-10.

## Discovering a networked printer

If the printer runs OctoPrint, Klipper with Moonraker, or PrusaLink, read it
instead of typing it in:

1. Ask the person for the controller's URL. If it needs an API key, ask them
   to set it on the gateway in an environment variable starting
   `METAFORGE_MACHINE_` (for example `METAFORGE_MACHINE_CR10_KEY`). Never ask
   them to paste the key to you.
2. `library.discover_machine` with `connection: {protocol, url,
   credential_env}`. It returns a proposed instance (build volume or axis
   travel, nozzle, current state, each recorded as `machine_report` with
   where and when it was read), what the controller reported, and the
   figures it could not report (minimum wall and feature, tolerance,
   overhang, materials).
3. Show the person the entry and the missing figures. Add any they can
   source; leave the rest out.
4. `library.register_machine` with the entry (held for their approval).

Discovery reads only; it registers nothing and changes nothing on the printer.

## Recording a machine

1. `library.list_machines` with `layer: model` and the `kind`. If the model
   is there, use its id.
2. If not, find the spec sheet (`web.search`, `web.fetch`) and register the
   model: `library.register_machine` with `layer: model`, `kind`, `make`,
   `model`, `processes` (for example `["fdm"]`) and `capabilities`, each
   `{value, unit, source_kind: "manufacturer_spec", source_ref: <url>}`.
3. Register the instance: `layer: instance`, `name`, `kind`, `model_id`,
   `route`, `status`, `location`, and only the capabilities that differ from
   the model or were measured.
4. Confirm with `library.get_machine` once the person has approved.

Capability names and units: `build_volume_mm` or `travel_mm` (`[x, y, z]`,
mm); `nozzle_mm`, `layer_height_min_mm`, `min_feature_mm`, `min_wall_mm`,
`tolerance_mm`, `min_bend_radius_mm`, `min_tool_diameter_mm`,
`max_sheet_thickness_mm` (mm); `max_overhang_deg` (deg); `max_part_mass_kg`
(kg); `spindle_max_rpm` (rpm); `laser_power_w` (W); `materials` (a list of
names).

## Using a machine in a design

`library.machine_limits` with the instance id returns:

- `candidate`: pass it to `rank_manufacturing_processes` as one candidate
  (envelope, minimum feature, tolerance, materials, availability, sources).
- `limits`: pass them to `check_manufacturability` (envelope, wall thickness,
  feature size, overhang angle, bend radius, inside corner radius from the
  tool diameter), each citing its source.
- `needs_review`: the figures that are assumptions. Name them at the gate.

In a design flow, the run's manufacturing context lists the machines; in the
route phase rank only processes those machines (or a vendor) can run, and in
the DFM phase check the design against the chosen machine's limits.

## Checklist

- [ ] looked for an existing model before adding one
- [ ] every capability sourced, or left out
- [ ] connection holds a `credential_env` name, never a secret
- [ ] told the person the write is waiting for their approval
