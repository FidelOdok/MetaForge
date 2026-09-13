# record_compliance_checklist

Generates a compliance checklist (same YAML-regime logic as
`generate_checklist`) and persists it as a `COMPLIANCE_CHECKLIST` work
product via `twin.commit_compliance_checklist` -- the reviewable, twin-
backed counterpart to what `generate_checklist` only computes in-session.

## What it does

1. Loads the target markets' YAML regime definitions (same
   `ChecklistGenerator` as `generate_checklist`) and generates the
   deduplicated checklist.
2. Calls `twin.commit_compliance_checklist`, which renders a markdown
   table and persists it.

## Input

`project_id`, `product_category` (default `consumer_electronics`),
`target_markets` (UKCA, CE, FCC, PSTI).

## Output

`node_id`, `target_markets`, `items`, `total_items`, `coverage_percent`,
`generated_at`.

## Limitations

Not idempotent -- re-running creates a new node each time; no dedup against
a prior checklist for the same project/markets.
