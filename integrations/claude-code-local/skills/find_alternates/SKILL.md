---
name: find_alternates
description: Find and rank up to three alternate parts for a BOM part that is risky to source, scoring each candidate on spec compatibility, availability, price and supply-risk reduction, using MetaForge's component search and live distributor data. Use when the user asks for an alternate, substitute, second source, drop-in replacement or cross-reference for a part, or when a part is out of stock, end of life, NRND, single-source or has a long lead time.
domain: supply_chain
---

# find_alternates

Given one original part and the specs a replacement must meet, find
candidates, drop those that are not compatible, and rank the rest. The
ranking weights compatibility 40%, availability 30%, price 20% and
supply-risk reduction 10%, and keeps the top three. It recommends; it does
not change the BOM.

## When to use it

- "The STM32F405 is on a 30-week lead time. What can replace it?"
- "Find a second source for this LDO."
- "Is there a drop-in for this capacitor in the same package?"
- `score_bom_risk` flagged a part as high or critical.

Not for:

- Choosing a part for a new need with no original: use
  `component.search_intent` or `component.search_parametric` directly.
- Scoring a whole BOM: use the `score_bom_risk` skill.
- Committing the substitution: after the user picks one, use
  `twin.record_component_selection` (step 6).

## What you can and cannot do over MCP

The skill's ranking engine runs in the MetaForge harness. No MCP tool runs
it, so you apply the same rules yourself, below, and show your working.
Label the result as ranked by these rules, not as the server's output.
Gathering the candidates and their data you can do with real tools.

## Tools and profile

| Step | Tool | Profile |
|---|---|---|
| Check the connection | `health.check` | every profile |
| Find the original on the BOM | `twin.find_by_property`, `twin.get_node` | every profile |
| Candidate search by specs | `component.search_parametric` | `electronics` |
| Candidate search from a description | `component.search_intent`, `knowledge.populate_bom` | `electronics` |
| Distributor keyword search and part detail | `digikey.search`, `mouser.search`, `digikey.get_product`, `mouser.get_product` | full tool set only |
| Stock, price, lead time, MOQ per distributor | `distributors.resolve_offers` | `electronics` |
| Check a spec in the datasheet | `knowledge.extract` (full set), `knowledge.search`, `web.fetch` (`core`) | as listed |
| Commit the chosen part | `twin.record_component_selection` | `electronics` |
| Record why | `twin.record_decision` | every profile |

The skill's definition lists `distributors.resolve_offers` and
`component.search_parametric` (both optional): the ranker itself calls no
tool, and ranks the candidates you pass it.

## Inputs you need before you start

Ask the user for anything missing. Do not fill these in with defaults.

| Input | Why it matters | Example |
|---|---|---|
| Original MPN | What is being replaced | `AP2112K-3.3TRG1` |
| Package | A different package is rejected outright | `SOT-23-5` |
| Specs that must be met or exceeded | Only these are compared | `voltage_rating`, `current_rating`, `tolerance` |
| Original manufacturer | A different manufacturer earns a small bonus | `Diodes Inc` |
| Original unit price | Needed to compare price; without it every candidate scores neutral | `0.38` USD |
| Required quantity | For stock sufficiency and price breaks | `500` |

Spec values come from the user, the BOM item's `specifications`, or the
original's datasheet (cite it). The six specs the rules compare are
`voltage_rating`, `current_rating`, `capacitance`, `resistance`,
`tolerance`, `temperature_range`. For each numeric one a candidate passes
when its value is **greater than or equal** to the original's. That suits
ratings, not tolerance (a smaller tolerance is better) or ranges: for those,
compare by hand and tell the user which way you compared.

## Procedure

### 1. Pin down the original

Find it with `twin.find_by_property` (`node_type: "BOMItem"`,
`property: "part_number"`, `value: <MPN>`) and read `specifications`,
`manufacturer`, `unit_cost`. Agree the must-meet specs with the user.

### 2. Gather candidates

Use what your profile has; more than one source is better.

- `component.search_parametric` with `category` and `filters` built from
  the must-meet specs (an unknown field name is an error, not an empty
  result: fix the name).
- `component.search_intent` with a plain description when the category is
  unclear.
- On the full set, `digikey.search` / `mouser.search` with the function and
  package, then `digikey.get_product` / `mouser.get_product` per candidate
  for `package`, `specs`, `lifecycle_status` and `datasheet_url`.

Drop the original MPN itself from the candidates.

### 3. Get live supply data

Call `distributors.resolve_offers` once with every candidate and the
original: `items: [{"mpn": ..., "required_qty": <qty>}, ...]`. For each
part note the number of distributors with offers, the top offer's
`stock_qty`, `lead_time_days`, `moq` and `unit_price_at_qty`, and the
`status` (`ok`, `insufficient_stock_everywhere`, `no_offers_found`).

### 4. Score each candidate

**Compatibility (0 to 100).** Start at 100.

- Package known on both sides and different: score 0, discard.
- Per must-meet spec: candidate value missing, minus 5; numeric and below
  the original, minus 15; non-numeric and not equal, minus 10.
- Different manufacturer from the original: plus 5, capped at 100.

**Availability (0 to 100).** Start at 100. Stock 0: minus 60; under 100:
minus 30; under 1000: minus 10. Lead time over 8 weeks: minus 40; over 2
weeks: minus 20 (weeks = days / 7).

**Price (0 to 100).** Candidate price over original: 0.8 or less, 100; up
to 1.0, 80; up to 1.2, 60; up to 1.5, 40; above, 20. Either price unknown:
50.

**Risk reduction.** The original's supply-risk score minus the candidate's
(floor 0), each computed with the six-factor rules in the `score_bom_risk`
skill. Normalise as min(100, reduction x 2).

**Composite** = 0.40 x compatibility + 0.30 x availability + 0.20 x price
+ 0.10 x normalised risk reduction. Rank by composite, keep the top three.

### 5. Recommend

- No compatible candidate: "No suitable alternates found for <MPN>."
- Original risk above 50 and the best alternate reduces it by more than 10:
  recommend substituting it, stating the reduction.
- Otherwise: the current part is acceptable, alternates exist.

Before calling any candidate a drop-in, check pinout and the critical specs
against its datasheet (`knowledge.extract` or `web.fetch` on
`datasheet_url`). Say which you checked; an unchecked candidate is a
lead, not a drop-in.

### 6. If the user picks one

Ask before writing. Call `twin.record_component_selection` with `mpn`,
`manufacturer`, `category`, `purchase_unit` (`discrete_part` or
`cots_assembly`), `quantity`, `distributor`, `unit_cost_usd` (only if
priced in USD), `datasheet_url`, `source: "manual"`, `project_id`, plus
`item_key` of the original BOM item and a `change_reason` such as
"Second source: original on 30-week lead time", so it revises that item
instead of adding a duplicate. Then `twin.record_decision` with the
rejected candidates as `alternatives`.

## What to report

Per alternate: MPN, manufacturer, compatibility, availability (stock and
lead time), price comparison (`lower` under 0.95x, `similar` up to 1.05x,
`higher` above, or `unknown`), risk reduction, composite, and which specs
were datasheet-checked. Then the recommendation, and what was recorded.

## Checks before you report

- [ ] Must-meet specs agreed with the user and sourced
- [ ] Package mismatches discarded, not down-ranked
- [ ] Stock, price and lead time came from a tool call, not memory
- [ ] Specs where "higher is better" does not hold were compared by hand
- [ ] Nothing was written to the BOM without the user choosing it

## Failure handling

| Symptom | Likely cause | What to do |
|---|---|---|
| `component.*` / `distributors.resolve_offers` not in your list | Not on `electronics` | Ask the user to reconnect with `?profile=electronics`. |
| `digikey.*` / `mouser.*` not in your list | Served only on the full tool set | Use `component.*` and `distributors.resolve_offers`; say so. |
| Distributor search returns an empty list | Credentials missing or API failed (it never raises) | Do not report "no alternates exist"; say the search returned nothing. |
| `no_offers_found` for a candidate | No configured distributor carries it | Availability unknown; score stock 0 and say why. |
| `component.search_parametric` errors on a field | Unknown spec name | Fix the field name; do not drop the filter silently. |
| A write is held / `approval_required` | Writes need a person | Tell the user where it waits; do not retry or reword it. |
| `-32001` naming an adapter | That adapter's container is down | Report it; do not loop. |

## Limits

- Compatibility covers only the specs supplied and six named fields; it does
  not read a datasheet for you. Pinout, footprint and behaviour need a
  separate check.
- Supply data is a snapshot from when you called the tools.
- At most three alternates are returned.
- Ranking, not substitution: the BOM changes only through step 6.
