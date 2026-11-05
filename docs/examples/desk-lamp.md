# Desk lamp golden path

The desk lamp is MetaForge's first multidisciplinary demonstration: one low
voltage, USB-C powered, dimmable desktop lamp with a printable base, a curved
arm and head, an LED module, a diffuser and a driver/control board. The goal
is a traceable, revision-compatible prototype manufacturing package, not a
claim of certification or a manufactured lamp.

This page covers **step 1**: the product definition and the cross-domain
interface contracts the later steps build against. CAD, EDA, firmware,
simulation and release are later steps; until they run, the demo reports them
as missing instead of pretending.

## The product definition

The lamp is defined once, as data, in
[`examples/desk_lamp/definition.json`](https://github.com/FidelOdok/MetaForge/blob/main/examples/desk_lamp/definition.json),
with the written brief beside it in `examples/desk_lamp/PRD.md`:

- **The prompt and brief.** "Design a modern desktop lamp with adjustable
  brightness, a compact base, a curved arm and a soft diffuser. Make it
  suitable for 3D printing." The Create brief items say which are confirmed
  and which are assumed, and every size is an estimated measurement.
- **References with roles.** The written brief is a `specification`
  reference. No reference image ships with the example and none is
  generated; add your own photo in Create and choose its role.
- **The manufacturing context.** FDM for the base, arm and head shell
  (stated); a translucent material for the diffuser (an assumption, on the
  diffuser's own context); low voltage over USB-C; one functional prototype.
- **The concept assembly.** Base, arm (curved), head, diffuser, LED module,
  driver PCB, USB-C connector and the brightness control (a button or a rotary
  encoder, not decided yet), each with its component id (`CMP-base`,
  `CMP-arm`, ...) from the first seed.
- **Four interface contracts**, described below.

## Seeding the project

```bash
curl -X POST http://localhost:8000/v1/examples/desk-lamp/seed
```

The seed creates the project `Desk lamp (golden path)` and writes, through the
services the rest of the gateway uses: the four contracts (each `proposed`),
the manufacturing context (`MFC-PROJECT`, `MFC-DIFFUSER`, `MFC-DRIVER-PCB`),
the concept assembly as a design decision, and the Create brief.

It is **idempotent and never overwrites**. Running it again finds the project
by name and fills only what is missing: a contract that exists is kept even if
someone changed it, a context field is written only while it is unknown, the
assembly is recorded only when the project has none, and a brief item only
when it is absent. A project with the same name that the seeder did not
create is refused with `409` and nothing is written. `GET /v1/examples` says
whether the definition is installed in this gateway.

## Interface contracts

A contract is a typed, versioned agreement between domains, stored as a
definition item in the twin (see
[interface contracts](../twin_schema.md#interface-contracts) for the record,
the checks and the routes):

| Contract | Agreed between | What it couples |
|----------|----------------|-----------------|
| `IFC-CAD-EDA` | mechanical, electronics | PCB outline, clearance, mounting holes, component height, LED position, USB-C position and cut-out, keep-outs, cable routing |
| `IFC-EDA-FIRMWARE` | electronics, firmware | controller, LED PWM pin, control input pins, driver limits, interface version |
| `IFC-SIMULATION` | mechanical, electronics, simulation | geometry revision, materials, LED power and heat, ambient and convection assumptions |
| `IFC-MANUFACTURING` | mechanical, electronics, manufacturing | print files, Gerbers, drill, pick-and-place, BOM, assembly instructions, firmware binary, all from the current revisions |

Every value says how far it can be trusted: `stated`, `estimated`, `assumed`
or `unknown`. The controller, the pins and the LED heat are `unknown` in the
seed, because no schematic or LED module exists yet.

A contract stays `proposed` until every owning domain agrees to its current
interface version. Agreement is a person's decision, recorded as a design
decision:

```bash
curl -X POST http://localhost:8000/v1/twin/projects/$PID/contracts/IFC-CAD-EDA/agree \
  -H 'content-type: application/json' -d '{"domain": "mechanical"}'
```

When the base CAD and the PCB layout exist, link them so the checks and the
impact walker can read them:

```bash
curl -X POST http://localhost:8000/v1/twin/projects/$PID/contracts/IFC-CAD-EDA/links \
  -H 'content-type: application/json' -d '{"role": "pcb", "item_key": "PCB-MAIN"}'
```

## Checking the contracts

`POST /v1/twin/projects/{id}/contracts/check` evaluates every contract against
the items' current revisions. Each check is `satisfied`, `violated` (with the
numbers), `not_evaluated` (with the missing input and the step that would
produce it) or `stale`. For example, the board must fit the base's internal
envelope with the agreed clearance, the mounting holes must sit on the
bosses, the USB-C socket must line up with its cut-out, the pins in the
firmware must be the ones in the EDA pin map, and the LED heat estimate must
exist for the thermal analysis.

Each result is recorded and pinned to the revisions it read. When the base
changes, the recorded check goes stale, the change's impact report names the
electronics side, and checking again reports the violation with its values.
That is the groundwork for the "make the lamp head 20% smaller" change later
in the golden path.

## Where it shows

- **Create:** under the journey, a short "Parts that must fit together" list
  in plain words, for example "The circuit board must fit inside the base:
  not checked yet".
- **Engineering:** the **Interfaces** tab of the inspector lists each
  contract with its coupled parameters (value, unit, tolerance, owner,
  basis), its links, agreements, status and the evidence of its latest check,
  with **Check now** and per-domain **Agree** actions.

## The demo runner

```bash
python scripts/demo_lamp.py --gateway http://localhost:8000 --project-dir desk-lamp
```

The runner seeds the project, checks every contract, prints the status table
and the missing steps, and writes `desk-lamp/.forge/demo_manifest.json`: the
seed outcome, each contract's status and check record id, every golden-path
step as `produced` (with item revisions, links and run ids) or `missing` (with
the step that would produce it), and the known limitations. The same manifest
is available from `GET /v1/examples/desk-lamp/projects/{id}/manifest`.

## Known limitations

- Checks run when asked; a change marks the last check stale rather than
  re-running it.
- Positions are compared in the frame both sides record them in; no frame
  transform is applied.
- Assembly instructions have no producing step yet.
- No reference image ships with the example.
