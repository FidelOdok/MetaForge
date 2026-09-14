# create_procurement_record

Persists a purchase-order-style procurement record as a
`PROCUREMENT_RECORD` work product via `twin.commit_procurement_record`,
usually linked back to the BOM it was sourced from.

## What it does

1. Takes line items (part number, quantity, unit cost, distributor, lead
   time) and an optional source BOM work-product id.
2. Verifies the source BOM exists in the Twin, if given.
3. Calls `twin.commit_procurement_record`, which computes total cost and
   max lead time and renders the record.

## Input

`name`, `line_items` (part_number, description, quantity, unit_cost,
currency, distributor, lead_time_days), `notes`, `bom_work_product_id`.

## Output

`node_id`, `line_item_count`, `total_cost`, `currency`, `max_lead_time_days`.

## Limitations

All line items in one record must share a currency -- the underlying tool
rejects mixed currencies; split into separate records per currency instead.
Not idempotent -- re-running creates a new node each time.
