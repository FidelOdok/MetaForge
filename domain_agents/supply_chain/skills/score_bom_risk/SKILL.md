# score_bom_risk

Score supply-chain risk for a BOM across single-source, lead time, lifecycle, price volatility, stock level, and compliance factors.

## What it does

1. Takes a project id and a list of BOM items as input
2. For each item, gathers distributor data and scores the risk factors (single-source, lead time, lifecycle stage, price volatility, stock level, compliance)
3. Combines the factors into a per-item risk score
4. Returns per-item scores and a roll-up so the riskiest parts surface first

## Tools Required

- `distributor_search` -- stock, lead-time, lifecycle, and pricing signals per part

## Input

- `project_id` -- project identifier
- `bom_items` -- list of BOM items (mpn, manufacturer, quantity, description, optional distributor_data)

## Output

- per-item risk scores across the six factors plus a combined score, and a
  BOM-level roll-up highlighting the highest-risk parts

## Limitations

- Scores reflect the distributor data available at scoring time, not live markets
- Lifecycle/compliance signals depend on distributor coverage of the part
- Produces scores/flags; mitigation (finding alternates) is `find_alternates`
