# Formgraph primitives

A product config is a selection of these 18 primitives. The engine implements all of them; a config only chooses and sets them up.

## Intent

| Primitive | Config key | Notes |
|---|---|---|
| Scenario | `scenarios[]` | Plain-language situation with the requirement ids that prove it |
| Requirement | `requirements[]` | EARS sentence with values, priority (Must, Should, Off) and a source |
| Decision | `decisions` | Behaviour in situations requirements do not cover |

## Graph

| Primitive | Config key | Notes |
|---|---|---|
| Parameter | `form.params`, `form.ranges` | Slider value and range per key (W, P, H, R, F, A, t, sn, sw, bn, bd, rt, rh) |
| Node | built by the engine | Form ops, Math, electronic blocks |
| Port | built by the engine | Typed: number, shape, power, I2C, UART, SPI, GPIO, USB, RF |
| Link | built by the engine | Same-type ports only, no loops. Electronics are wired automatically |

## Physical

| Primitive | Config key | Notes |
|---|---|---|
| Form op | `form.template` | `enclosure`: soft box, shell |
| Feature | `form.features` | `slots`, `bosses`, `ribs` |
| Block | `functions`, `power` | Functions add blocks; the charger and antenna are added when needed |
| Part | placed by the engine | A block placed inside with a clear zone |
| Cable | routed by the engine | Wire size, connector, route, length |

Process and material (`process`) are context for every Limit.

## Rules

| Primitive | Where | Notes |
|---|---|---|
| Limit | engine | Allowed range for a parameter role; drives red bands and clamping |
| Check | engine | Value, limit, pass, warn or fail, linked BOM lines |
| Fix | engine | One-click change offered with a failing check or requirement |

## Proof

| Primitive | Config key | Notes |
|---|---|---|
| Simulation | `simulations` | fill, drop, thermal, press, plus the power budget and behaviour model |
| Job | engine | Background checks: routing, week of use, worst-case brownout, 3D heat. Cloud solver runs are estimated |
| Gate | engine | Intent, Concept, Simulation, Validation |

## Output

| Primitive | Config key | Notes |
|---|---|---|
| BOM line | `nominations` | Part numbers already known, keyed by line id |
| Revision | engine | Fingerprint, date and gate states at release |

## Rules that join them

1. Every requirement is proved by at least one check or simulation.
2. Every limit belongs to a parameter role. It shows as a red band and holds the slider.
3. Every block needing an opening creates a feature. Every link between separate parts creates a cable.
4. Every check links to the BOM lines it affects. A failing check marks those lines as conflicts, and conflicts block release.
5. Every job is keyed by a fingerprint of its inputs, so only changed inputs rerun.
6. Every parameter, check and simulation has a level: Simple shows it, or only Engineer does.
