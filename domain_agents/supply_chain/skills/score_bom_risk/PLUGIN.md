---
description: Score the supply-chain risk of a project's BOM, part by part, across single source, lead time, lifecycle, price volatility, stock level and RoHS/REACH compliance, and roll it up so the riskiest parts surface first. Use when the user asks how risky the BOM is, which parts are hard to buy, single-source, long lead, end of life or obsolete, whether the BOM is ready to build, or for a supply-chain risk report before a build or gate review.
---

# score_bom_risk

Give every BOM line a supply-risk score from 0 to 100 built from six
weighted factors, classify it, flag the high and critical ones, and give the
BOM an overall score (the average of its parts). The point is to show which
parts will stop a build, and why, before anyone orders.

## When to use it

- "How risky is our BOM?"
- "Which parts are single-source or end of life?"
- "Can we build 50 of these in six weeks?"
- Before raising a procurement record, or before a gate review.

Not for:

- Finding replacements for a risky part: use the `find_alternates` skill.
- Raising the order: use the `create_procurement_record` skill.
- Safety hazards: use the `analyze_hazards` skill.

## What you can and cannot do over MCP

The scorer runs in MetaForge, not in any MCP tool. Two honest routes:

1. **The dashboard's BOM risk view** runs the real scorer server-side on the
   project's committed BOM items, with live distributor data. If the user
   can open the dashboard, that is the authoritative number; point them to
   it.
2. **Over MCP you gather the same inputs with real tools and apply the same
   rules yourself**, below. Label the result as computed by these rules
   from the data you fetched.

## Tools and profile

| Step | Tool | Profile |
|---|---|---|
| Check the connection | `health.check` | every profile |
| Find the project | `project.open` | every profile |
| List BOM lines | `twin.find_by_property`, `metaforge://twin/hierarchy/<project_id>` (resource) | every profile |
| BOM from a schematic | `kicad.export_bom` | `electronics` |
| Sources, stock, lead time, MOQ, price | `distributors.resolve_offers` | `electronics` |
| Lifecycle status | `digikey.get_product`, `mouser.get_product` | full tool set only |
| Record a decision on a flagged part | `twin.record_decision` | every profile |
| Save the report as a document | `twin.record_document` | `core` |

The skill's definition names a tool `distributor_search`; no tool has that
id. `distributors.resolve_offers` is the real source.

## Inputs you need before you start

| Input | Why it matters | Example |
|---|---|---|
| Project | Whose BOM | `Drone FC` |
| Build quantity | Stock and MOQ are judged against it | `50` units |
| Deadline, if any | Ranks offers that arrive in time | `42` days |
| The BOM, if not in the twin | Lines to score | a KiCad schematic path, or a list from the user |

Ask for the build quantity. A line's own quantity times the build quantity
is what the distributor must have in stock; do not assume one unit.

## Procedure

### 1. Get the BOM lines

1. `project.open` with the user's words; ask if several match.
2. Call `twin.find_by_property` with `node_type: "BOMItem"`,
   `property: "project_id"`, `value: <project_id>` (raise `limit` to 200
   for a large BOM). Keep `part_number`, `manufacturer`, `quantity`.
   Skip lines with no `part_number` and list them as unscored.
3. If the twin has no BOM items, and the user has a KiCad schematic, use
   `kicad.export_bom` (`schematic_file`). Otherwise ask the user for the list.

### 2. Fetch supply data

Call `distributors.resolve_offers` with `items: [{"mpn": <part_number>,
"required_qty": <line qty x build qty>, "deadline_days": <if given>}, ...]`.
For each result, from all offers (`offers` plus `insufficient_offers`):

| Scorer input | Take it from |
|---|---|
| `num_sources` | Number of offers. `no_offers_found` means 0. |
| `lead_time_weeks` | Top offer's `lead_time_days` / 7 |
| `stock`, `moq` | Top offer's `stock_qty`, `moq` |
| `prices` | Each offer's `unit_price_at_qty` |
| `lifecycle` | `lifecycle_status` from `digikey.get_product` or `mouser.get_product` for the top offer's distributor. `resolve_offers` does not fill it. |
| `rohs_compliant`, `reach_compliant` | No MetaForge tool returns these. Only the user or a supplier declaration can supply them. |

The top offer is the first of `offers`, or of `insufficient_offers` when
there is no sufficient one. If offers span currencies
(`currency_mismatch: true`), do not compute price volatility; mark it
unknown.

### 3. Score each part

Each factor scores 0, 50 or 100:

| Factor (weight) | 0 | 50 | 100 |
|---|---|---|---|
| Single source (0.25) | 3+ sources | 2 sources | 0 or 1 source |
| Lead time (0.20) | under 2 weeks | 2 to 8 weeks | over 8 weeks |
| Lifecycle (0.20) | active | NRND or unknown | EOL or obsolete |
| Price volatility (0.15) | CV under 0.1, or fewer than 2 prices | CV 0.1 to 0.3 | CV 0.3 or more |
| Stock level (0.10) | 10x MOQ or more | MOQ to 10x MOQ | below MOQ |
| Compliance (0.10) | RoHS and REACH | one of them | neither |

CV is the standard deviation of `prices` (population) divided by their mean.
Part score = the weighted sum (weights total 1.0), rounded. Levels: 0 to 25
`low`, 26 to 50 `medium`, 51 to 75 `high`, 76 to 100 `critical`. High and
critical are flagged.

**Missing data.** The scorer turns missing inputs into numbers, and not
consistently: a missing source count scores as single source (100),
missing stock as 0 in stock (100), missing compliance as neither (100),
missing lifecycle as unknown (50), but a missing lead time as 0 weeks (0)
and missing prices as stable (0). Do not hide this. For every factor you
had no data for, mark it "no data" in the table, compute the score as the
rules do, and say which factors were defaulted and in which direction. In
particular, compliance will be 100 for every part unless the user gives you
RoHS/REACH status; say that this alone adds 10 points to every part.

### 4. Roll up

- `overall_score` = the mean of part scores, rounded.
- Counts of `critical`, `high`, `medium`, `low`.
- Sort parts by score, highest first.

### 5. Report and act

- Overall score and the four counts.
- A table per part: MPN, manufacturer, score, level, each factor's score
  with its reason (for example "single source", "26 weeks", "EOL"), and
  which factors had no data.
- The flagged parts first, each with the factor that drives it.
- For flagged parts, offer the `find_alternates` skill.

Nothing is written by default. If the user wants the report kept, record it
with `twin.record_document` (`document_type: "documentation"`, a markdown
table as `content`, the numbers in `metadata`, `project_id`). If they decide
to accept a risky part, record that with `twin.record_decision` including
the alternatives they rejected.

## Checks before you report

- [ ] Required quantity included the build quantity
- [ ] Every stock, lead time and price came from a tool result
- [ ] Defaulted factors are labelled, with their direction
- [ ] Compliance was not presented as a real finding without user data
- [ ] Unscored lines (no part number, no data) are listed, not dropped
- [ ] You said whether the dashboard's server-side score is available

## Failure handling

| Symptom | Likely cause | What to do |
|---|---|---|
| `distributors.resolve_offers` not in your list | Not on `electronics` | Ask the user to reconnect with `?profile=electronics`. |
| `digikey.get_product` not in your list | Served only on the full tool set | Score lifecycle as unknown and say so. |
| Every part `no_offers_found` | No distributor configured or credentials missing | Do not report "all parts single-source"; say supply data is unavailable. |
| `twin.find_by_property` returns no BOM items | No BOM committed, or project scope not bound | Pass `project_id` explicitly; otherwise ask for the BOM. |
| `-32001` naming `kicad` | KiCad adapter container down | Tell the user; ask for the BOM another way. |
| A write is held / `approval_required` | Writes need a person | Tell the user where it waits; do not retry. |

## Limits

- Scores reflect distributor data at the moment you fetched it.
- Compliance flags are not sourced by any MetaForge tool.
- Lifecycle needs a per-distributor product lookup, available only on the
  full tool set.
- It scores and flags. Finding a fix is `find_alternates`; changing the
  BOM is `twin.record_component_selection`.
