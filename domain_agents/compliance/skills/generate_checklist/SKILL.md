# generate_checklist

Generate a compliance checklist for target markets, deduplicating standards shared across regulatory regimes.

## What it does

1. Takes a project id, product category, and target markets as input
2. Expands each market/regime into its applicable standards
3. Deduplicates standards shared across regimes (e.g. a single EMC standard covering multiple markets)
4. Returns a consolidated checklist with an evidence-coverage estimate

## Tools Required

- (none) -- pure logic over the built-in regulatory-regime catalog

## Input

- `project_id` -- project identifier
- `product_category` -- a label recorded with the checklist (default `consumer_electronics`)
- `product_features` -- optional: `radio`, `mains_powered`, `battery`, `connected`, `body_worn`. Items needing a feature the product lacks are excluded; left out, every item is kept and the conditional ones are listed
- `evidence` -- optional: evidence already held, by item id (`status`, `work_product_id`)
- `target_markets` -- list of `ComplianceRegime` values to cover

## Output

- `project_id` -- the project the checklist is for
- `target_markets` -- markets included
- `items` -- generated checklist items (standard, requirement, evidence status)
- `total_items` -- total item count after deduplication
- `coverage_percent` -- percentage of items whose evidence status is not `MISSING`
- `excluded_items` -- items left out, with the missing feature
- `conditional_items` -- items kept only because `product_features` was not stated

## Limitations

- Coverage of regimes/standards is bounded by the built-in catalog
- Produces the checklist only; it does not gather or verify the evidence itself
- Evidence coverage is a completeness estimate, not a certification verdict
