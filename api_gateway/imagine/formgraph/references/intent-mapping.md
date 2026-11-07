# From brief to primitives

Use this when writing a product config from a plain-language brief. Every number you put in a requirement needs a `source`. If you used a default from this page, write the default's source.

## The six questions

Infer as many as you can from the brief. Ask only for what is missing, at most three at once, in plain words.

| Question | Feeds |
|---|---|
| Where is it used, and by whom? | drop, ip, touch, ambient, scenarios |
| What does it do? | functions, interval, acc, resp |
| How long must it run between charges? | life, battery |
| How is it powered? | battery, e_usbc, board |
| How does it connect? | e_lte or e_lora, cov |
| What goes wrong? | scenarios, drop, ip, decisions |

## Phrases to functions

| Brief says | Add |
|---|---|
| temperature, humidity, air quality, climate | `e_th` |
| movement, tamper, vibration, tilt, shock | `e_imu` |
| location, tracking, where it is, GPS | `e_gnss` |
| cellular, LTE, anywhere, mobile network | `e_lte` |
| LoRa, long range, low power network, farm, gateway | `e_lora` |
| button, press, acknowledge, wake | `e_btn` |
| status light, indicator, LED | `e_led` |
| alarm, beep, audible | `e_buzz` |
| lamp, light source, high-power or dimmable LED | `e_ldrv` (a constant-current buck LED driver), with `e_led` only for a status light |
| USB-C, rechargeable, charging, plug in | `e_usbc` |

Choose one radio. If the brief names neither but needs to report, ask which network is available, or pick `e_lte` for anywhere and `e_lora` for sites with a gateway and say why.

## Power and board

| Brief says | Choose |
|---|---|
| sensor, tracker, logger, months on a battery | `board: mcu` |
| Linux, camera, display, Raspberry Pi, heavy compute | `rpiz2w` (small) or `rpi4` (more compute, 6 W) |
| own PCB with known size | `custom` with `power.custom` L, W, H in mm, P in W, M in g |
| days to weeks on battery, handheld or desk | `battery: c18650` |
| thin, small, wearable, under 2 Ah | `battery: p103450` |
| mains, always plugged in | `battery: none` with `e_usbc` |

A 5 V board on a battery needs a boost; the page offers it as a fix. Prefer `mcu` for anything that must last more than a few days.

## Requirement defaults

| Requirement | Default | Source to record |
|---|---|---|
| interval | from brief; 30 min if it only says "regularly" | brief, or "assumed, regular reporting" |
| life | from brief; 7 days for handheld, 30 days for fixed sensors | brief, or "assumed, typical for the use" |
| resp | 0.5 s; 0.3 s if it acknowledges alerts | typical user expectation |
| cov | fair for towns, poor for rural or indoor basements, good for open sites | named coverage case |
| acc | 0.5 C for monitoring, 1.0 C for comfort readouts | monitoring practice |
| drop | 0.75 m desk, 1.0 m vehicle or bench, 1.2 m handheld or rail, 1.5 m pocket | named height |
| touch | 48 C plastic, 43 C metal | touch limits in the style of IEC 62368-1 |
| chgt | always Must when a Li-ion cell charges in the product | Li-ion charging limit |
| chgh | 10 h overnight, 6 h during a shift | named case |
| ip | none indoors, ip54 rain or splashes, ip67 immersion | named exposure |

Set requirements the brief does not touch to `Off` by leaving them out, unless they follow from a function (for example `chgt` whenever a battery charges in the product).

## Process from volume and look

| Volume | Choose |
|---|---|
| up to about 50, made on a desktop printer | `fdm` with `petg`, `pla`, `petgcf` or `asa` (`asa` outdoors) |
| under 300 | `sls` with `pa12`, or `cnc` with `al6061` for a metal look |
| 300 to 3,000 | `sls` or `cnc`; the page compares costs |
| over 3,000 | `im` |

Material rules:
- Any Li-ion cell inside: a V-0 or V-1 grade (`absfr`, `pcabsfr`) or metal.
- A radio or GNSS inside: never a metal enclosure. The page fails it and suggests POM-C.
- Outdoors in sun: prefer `pcabsfr` or `pc`.

Finish: `matte` by default for moulding, `tex` for rugged handheld (needs more draft), `gloss` for consumer desk products.
Fixings: `pt25` for moulded and printed parts, `ins25` where the lid is opened often, `tap25` for CNC.

## Form

Start from the closest example and adjust:

- **Size:** W from the board and battery plus about 20 mm. P from the footprint the user describes, 0.618 if unknown.
- **Height:** H from the stack. The page raises it automatically when the contents do not fit.
- **Features:**
  - `slots` only when venting or sensing outside air is needed and the product is not sealed.
  - `bosses` for a screwed lid.
  - `ribs` for anything bigger than 80 mm or pressed by hand.

## Decisions

Fill `decisions` only when the brief says what should happen. Otherwise leave them for the user to answer under Try it; the page flags them as open.

## Intent ledger

Split the brief into statements, usually one per clause, and record each in `programme.ledger` with `maps` and `status`:

| Status | Use when | `maps` |
|---|---|---|
| `mapped` | a built-in primitive expresses it | `req:<id>`, `fn:<id>`, `scenario:<id>`, `board:<id>`, `process:<id>`, `form:enclosure` |
| `custom` | it needs a custom requirement | `custom:<id>` of a `customRequirements` entry |
| `out-of-scope` | it belongs to another project or is not this product's job | empty, with a `note` saying where it goes |
| `unrepresentable` | nothing fits yet and the user must decide | empty, with a `note`; the builder warns until it is resolved |

Never leave a statement out of the ledger. The Define phase stays open until every statement is reviewed.

## Assumptions

Anything you chose that the brief did not state goes in `programme.assumptions` with an `id`, plain `text` and the `ref` it affects (for example `requirement cov`). The user confirms each on the page; Gate 1 and the Define phase wait for it.

## Custom requirements

Write each as an EARS sentence:

| Pattern | Template |
|---|---|
| Always | The device shall ... |
| Event | When ..., the device shall ... |
| State | While ..., the device shall ... |
| Unwanted | If ..., then the device shall ... |
| Optional | Where ..., the device shall ... |

Give each one:
- `measure` and `target`
- `pri`: Must or Should
- `verify`: test, analysis, inspection or demonstration
- `level`: subsystem, system or acceptance (the V-model pairing)
- `phase`: when it is verified
- `source`

The page creates a test for each and tracks its status.

## Programme fields

| Brief says | Set |
|---|---|
| sold in the UK, Europe, the US | `markets`: UK, EU, US |
| consumers, homes, retail | `segment: consumer` (UK PSTI applies to connected products) |
| businesses, sites, fleets | `segment: business` |
| factories, plant, OT networks | `segment: industrial` (IEC 62443) |
| lights | `sector: luminaire`; household appliances `household`; medical `medical`; vehicles `automotive`; aircraft or drones `aircraft` |
| outdoors | `environment.outdoor: true` |
| handheld, pocket | `human.handheld: true`; gloves `human.gloves: true`; wall mounted `human.mount: wall` |
| app, display | `software.app` or `software.display` |
| target price, margin | `business.price` in GBP, `business.margin` as a fraction |
| weight, power or cost limits | `budgets.mass_g`, `budgets.power_mW`, `budgets.cost_gbp` |
| lifetime | `lifecycle.targetLifeYears` |

For connected products, fill `firmware`:
- `ota`, `watchdog`, `secureBoot`, `signedUpdates`, `encryption`, `uniqueCredentials`, `secureStorage`
- `vulnPolicy` and `supportYears`. These are required by UK PSTI for consumer products.

Write `rationale` for:
- **process:** volume and look
- **material:** fire rating, outdoor use, metal or plastic
- **board:** power and compute
- **battery:** capacity and peak current
- **radio:** coverage and power
- **size:** contents and DFM limits

Add `tradeoffs` for any other decision with options, criteria, the choice and the reason.

## Environment

Fill the top-level `environment` block from where the product lives. If the brief doesn't say, use the closest row, record it as an assumption, and give the source.

| Brief says | opMin to opMax | storeMin to storeMax | sun | humidity | vibration | other |
|---|---|---|---|---|---|---|
| office, home, indoors | 10 to 35 | -20 to 60 | none | 10 to 80 | fixed | |
| warehouse, depot, unheated | -10 to 40 | -20 to 60 | none | 10 to 95, condensing | fixed | |
| outdoors, UK or northern Europe | -20 to 40 | -30 to 60 | partial or full | 10 to 100, condensing | fixed | outdoor, uvHours 1,200 to 2,000 |
| outdoors, hot climate | -5 to 50 | -20 to 70 | full | 5 to 95 | fixed | outdoor, uvHours 3,000 |
| on a drone dock or mast | -20 to 50 | -30 to 60 | full | 10 to 100, condensing | fixed | outdoor, altitude as an assumption |
| in a vehicle or on a trailer | -30 to 70 | -40 to 85 | partial | 10 to 95, condensing | vehicle | salt if roads are gritted |
| carried, pocket, handheld | -10 to 40 | -20 to 60 | partial | 10 to 90 | handheld | |
| on an aircraft or drone | -20 to 55 | -40 to 70 | full | 5 to 95 | aircraft | altitude_m from the ceiling |
| coastal, marine | as outdoors | as outdoors | full | 10 to 100, condensing | fixed | salt |

Rules the page enforces:
- **Charging below 0 °C:** a product that charges a Li-ion cell and can be below 0 °C needs `programme.firmware.chargeInhibit: true`. Otherwise the charge-window check fails.
- **Sealed and condensing:** a sealed product in a condensing environment needs `ventMembrane: true`. A membrane also lets vent slots pass IP54.
- **Sun:** the sun heats a dark body far more than a light one. If the charge window or electronics fail in the heat, the page offers a light colour first.
- **UV:** ABS and PP fail outdoors. Use PC/ABS, PC or a metal.
- **Salt:** salt air needs anodised aluminium and stainless fixings.
