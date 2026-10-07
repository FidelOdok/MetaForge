---
description: Build a regulatory compliance checklist for a product's target markets (UKCA, CE, FCC, PSTI) from MetaForge's built-in regime catalogue, with standards shared across regimes listed once. Use when the user asks what certifications, standards, declarations or test reports a product needs to sell in the UK, EU or US, asks for a compliance or certification checklist, or asks what evidence is still missing for market approval. Computes only; to save the checklist in the twin use record_compliance_checklist.
---

# generate_checklist

Turn a list of target markets into one checklist of what has to be shown
before the product can be sold there: each requirement, the standard it
cites, and the kind of evidence that satisfies it. A standard that two
regimes both cite (for example EN 62368-1 under UKCA and CE) appears once.
This skill computes and reports; it writes nothing to the twin.

## When to use it

- "What do we need to sell this in the UK and the EU?"
- "Give me a CE and FCC checklist for the controller board."
- "Which standards apply for UKCA, and what evidence does each need?"
- "Does PSTI apply to us, and what does it ask for?"

Not for:

- Saving the checklist as a reviewable work product: use the
  `record_compliance_checklist` skill (same catalogue, plus a write).
- Safety hazards of the design itself: use the `analyze_hazards` skill.
- Markets the catalogue does not cover (Canada ISED, Japan MIC, Australia
  RCM, Korea KC, ...): say they are not in the catalogue; do not improvise
  a list for them as if it came from MetaForge.

## Tools and profile

The handler is pure logic over a catalogue that ships with MetaForge
(`domain_agents/compliance/regimes/`). No MCP tool runs it and no MCP
resource exposes the catalogue, so the catalogue is reproduced below and you
build the checklist from it yourself.

| Step | Tool | Profile |
|---|---|---|
| Find the project | `project.open`, `project.get` | every profile |
| Read what exists | `metaforge://twin/brief/<project_id>` (resource) | every profile |
| Check for evidence already in the knowledge layer | `knowledge.search` | every profile |
| Read a standard's scope, if the user asks | `web.search`, `web.fetch` | `core` |
| Record why a regime was in or out | `twin.record_decision` | every profile |

## Inputs you need before you start

Ask the user for anything missing. Do not fill these in with defaults.

| Input | Why it matters | Example |
|---|---|---|
| Target markets | Selects the regimes | `UKCA, CE` |
| Product features | Decide which conditional items apply: `radio`, `mains_powered`, `battery`, `connected`, `body_worn` | `radio, battery, connected` |
| Product category | A label recorded with the checklist; applicability comes from the features | `consumer_electronics` |
| Project | Optional for a computed checklist, needed to look for evidence | `Drone FC` |

Map the user's words to regimes: "UK" is `UKCA` (and `PSTI` if the product
connects to a network or the internet), "EU" or "Europe" is `CE`, "US" or
"USA" is `FCC`. Ask before adding `PSTI`; do not assume a product is
connectable.

## The built-in catalogue

Evidence types: TR test report, DEC declaration, CERT certificate, TF
technical file, RA risk assessment. Rows marked * share a standard with
another regime.

**UKCA** (15 items)

| ID | Category | Requirement | Standard | Evidence |
|---|---|---|---|---|
| UKCA-SAF-001 * | safety | Product safety per EN 62368-1 | EN 62368-1:2020 | TR |
| UKCA-SAF-002 | safety | Electrical safety, insulation and grounding | BS 7671:2018 | TR |
| UKCA-SAF-003 | safety | Battery safety per IEC 62133 | IEC 62133-2:2017 | TR |
| UKCA-EMC-001 * | emc | EMC emissions per EN 55032 | EN 55032:2015+A1:2020 | TR |
| UKCA-EMC-002 * | emc | EMC immunity per EN 55035 | EN 55035:2017+A11:2020 | TR |
| UKCA-EMC-003 | emc | EMC technical documentation | SI 2016/1091 | TF |
| UKCA-RAD-001 | radio | Radio equipment conformity | SI 2017/1206 | CERT |
| UKCA-RAD-002 * | radio | Radio spectrum efficiency | EN 300 328 V2.2.2 | TR |
| UKCA-RAD-003 | radio | Radio receiver performance | EN 303 345 V1.1.7 | TR |
| UKCA-ROHS-001 | rohs | Restriction of hazardous substances | SI 2012/3032 | DEC |
| UKCA-ROHS-002 | rohs | RoHS material declarations from suppliers | SI 2012/3032 Schedule 2 | DEC |
| UKCA-ROHS-003 | rohs | Lead-free soldering compliance | IEC 61249-2-21 | TR |
| UKCA-WEEE-001 | weee | WEEE producer registration | SI 2013/3113 | CERT |
| UKCA-WEEE-002 | weee | WEEE marking and labeling | SI 2013/3113 Regulation 14 | DEC |
| UKCA-WEEE-003 | weee | Product recyclability documentation | BS EN 50625-1:2014 | TF |

**CE** (18 items)

| ID | Category | Requirement | Standard | Evidence |
|---|---|---|---|---|
| CE-LVD-001 | lvd | Low Voltage Directive safety assessment | 2014/35/EU | TR |
| CE-LVD-002 * | lvd | Product safety per EN 62368-1 | EN 62368-1:2020 | TR |
| CE-LVD-003 | lvd | LVD Declaration of Conformity | 2014/35/EU Annex IV | DEC |
| CE-EMC-001 * | emc | EMC emissions per EN 55032 | EN 55032:2015+A1:2020 | TR |
| CE-EMC-002 * | emc | EMC immunity per EN 55035 | EN 55035:2017+A11:2020 | TR |
| CE-EMC-003 | emc | EMC Declaration of Conformity | 2014/30/EU | DEC |
| CE-RED-001 | red | Radio Equipment Directive conformity | 2014/53/EU | CERT |
| CE-RED-002 * | red | Radio spectrum efficiency | EN 300 328 V2.2.2 | TR |
| CE-RED-003 | red | RED Declaration of Conformity | 2014/53/EU Annex VI | DEC |
| CE-RED-004 | red | Notified body opinion for non-harmonised | 2014/53/EU Article 17 | CERT |
| CE-ROHS-001 | rohs | RoHS compliance declaration | 2011/65/EU | DEC |
| CE-ROHS-002 | rohs | Material composition analysis | EN IEC 63000:2018 | TR |
| CE-ROHS-003 | rohs | Supplier RoHS declarations | 2011/65/EU Annex II | DEC |
| CE-WEEE-001 | weee | WEEE producer registration in target EU states | 2012/19/EU | CERT |
| CE-WEEE-002 | weee | WEEE marking (crossed-out wheelie bin) | EN 50419:2022 | DEC |
| CE-REACH-001 | reach | REACH SVHC substance screening | EC 1907/2006 | TR |
| CE-REACH-002 | reach | REACH SCIP database notification | EC 1907/2006 Article 33 | DEC |
| CE-REACH-003 | reach | Substance risk assessment | EC 1907/2006 Annex XV | RA |

**FCC** (10 items)

| ID | Category | Requirement | Standard | Evidence |
|---|---|---|---|---|
| FCC-15B-001 | part15_subpartb | Unintentional radiator emissions (Class B) | 47 CFR Part 15 Subpart B | TR |
| FCC-15B-002 | part15_subpartb | Conducted emissions measurement | ANSI C63.4-2014 | TR |
| FCC-15B-003 | part15_subpartb | Radiated emissions measurement | ANSI C63.4-2014 Section 8 | TR |
| FCC-15B-004 | part15_subpartb | FCC Part 15 Declaration of Conformity | 47 CFR 15.19 | DEC |
| FCC-15C-001 | part15_subpartc | Intentional radiator certification | 47 CFR Part 15 Subpart C | CERT |
| FCC-15C-002 | part15_subpartc | Transmitter spurious emissions | 47 CFR 15.209 | TR |
| FCC-15C-003 | part15_subpartc | Occupied bandwidth compliance | 47 CFR 15.247 | TR |
| FCC-SAR-001 | sar | SAR evaluation for portable devices | 47 CFR 2.1093 | TR |
| FCC-LBL-001 | labeling | FCC ID labeling on device | 47 CFR 2.925 | DEC |
| FCC-LBL-002 | labeling | Electronic labeling (e-label) compliance | 47 CFR 2.925(e) | TF |

**PSTI** (8 items)

| ID | Category | Requirement | Standard | Evidence |
|---|---|---|---|---|
| PSTI-PWD-001 | passwords | No universal default passwords | PSTI Act 2022 Schedule 1(1) | TR |
| PSTI-PWD-002 | passwords | Unique per-device passwords or user-set on first use | ETSI EN 303 645 5.1-1 | TR |
| PSTI-VDP-001 | vulnerability_disclosure | Published vulnerability disclosure policy | PSTI Act 2022 Schedule 1(2) | DEC |
| PSTI-VDP-002 | vulnerability_disclosure | Contact point for security researchers | ETSI EN 303 645 5.2-1 | DEC |
| PSTI-UPD-001 | updates | Defined minimum security update period | PSTI Act 2022 Schedule 1(3) | DEC |
| PSTI-UPD-002 | updates | Secure update delivery mechanism | ETSI EN 303 645 5.3-1 | TF |
| PSTI-SBT-001 | secure_boot | Secure boot chain verification | ETSI EN 303 645 5.7-1 | TR |
| PSTI-SBT-002 | secure_boot | Software integrity validation at boot | ETSI EN 303 645 5.7-2 | TF |

## Conditional items

Some items apply only to products with certain features. An item applies
when the product has **every** feature listed for it:

| Items | Needs |
|---|---|
| UKCA-SAF-001, UKCA-SAF-002, CE-LVD-001 to 003 | `mains_powered` |
| UKCA-SAF-003 | `battery` |
| UKCA-RAD-001 to 003, CE-RED-001 to 004, FCC-15C-001 to 003, FCC-LBL-001 | `radio` |
| FCC-SAR-001 | `radio` and `body_worn` |
| All PSTI items | `connected` |

Every other item applies to every product.

## Procedure

1. If a project is named, call `project.open` with the user's words as
   `query` and read `metaforge://twin/brief/<project_id>` to see what the
   product contains (radio, battery, mains power, network connection).
2. Confirm the target markets with the user, in the order they care about
   most. Only `UKCA`, `CE`, `FCC` and `PSTI` exist.
3. Ask which features the product has (or read them from the brief and
   confirm): `radio`, `mains_powered`, `battery`, `connected`, `body_worn`.
4. Build the checklist exactly as the handler does:
   - Walk the markets in alphabetical order (`CE`, `FCC`, `PSTI`, `UKCA`),
     whatever order the user gave. Within a market, take its rows top to
     bottom.
   - With features stated, leave out every item needing a feature the
     product lacks, and list it as excluded with the missing feature.
     Without features, keep every item and list the conditional ones as
     "applies only if the product has ...".
   - If a row's **exact** standard string was already taken by an earlier
     row, fold it into that row ("also satisfies UKCA-EMC-001") instead of
     listing it again. Only the four rows marked * can collide, so UKCA and
     CE together give 15 + 18 - 4 = 29 items before exclusions, with the CE
     ids kept.
   - Set each item's evidence status to `MISSING`, unless the user has
     confirmed evidence for it (then `UPLOADED`, `REVIEWED` or `APPROVED`).
5. `coverage_percent` is the share of items whose status is not `MISSING`.
   Do not raise it on the strength of a document you merely found.
6. Optionally, for each item, call `knowledge.search` (with `project_id`)
   for an existing test report, declaration or certificate. Report a match
   as "possible evidence found: <source_path>" next to the item, for the
   user to confirm. It does not change the status until they do.
7. If the user decides a regime or an item is out of scope, offer to record
   that with `twin.record_decision` (`title`, `rationale`, `alternatives`).

## What to report

- Markets included, in order, and `total_items`.
- The checklist grouped by regime and category: id, requirement, standard,
  evidence type, status.
- Which shared standards were folded and which item kept them.
- The features used, and the excluded items with the missing feature; or,
  if features were not stated, the conditional items as questions.
- `coverage_percent` and any possible evidence found.
- That nothing was saved, and that `record_compliance_checklist` saves it.

## Checks before you report

- [ ] Every market is one of the four regimes; any other was named as unsupported
- [ ] Deduplication used the exact standard string and alphabetical market order
- [ ] Every item's status is `MISSING` unless the user confirmed evidence
- [ ] Items were excluded only by the stated features, never by your own judgement
- [ ] You said the list comes from MetaForge's built-in catalogue

## Failure handling

| Symptom | Likely cause | What to do |
|---|---|---|
| User names a market not in the catalogue | Only four regimes are defined | Say so; offer the nearest covered regimes only if the user wants them. |
| `knowledge.search` returns nothing | No evidence ingested yet | Report "no evidence found", not "not needed". |
| Several projects match | Ambiguous name | Ask the user which one. |
| A write you were asked to make is held / `approval_required` | Writes need a person | Tell the user where it waits; do not retry. |
| `-32001` naming an adapter | That adapter's container is down | No adapter is needed here; continue without the lookup and say so. |

## Limits

- Coverage is bounded by the built-in catalogue: four regimes, a fixed item
  list, five product features, no version tracking of standards.
- Deduplication is by exact string. `2014/35/EU` and `2014/35/EU Annex IV`
  stay separate; one test report may in practice serve both regimes even
  where the strings differ.
- A checklist is a completeness aid, not a conformity verdict. A notified
  body or test lab decides compliance.
- Nothing is written to the twin by this skill.
