---
name: create_procurement_record
description: Record a purchase-order-style procurement record (line items with part number, quantity, unit cost, currency, distributor and lead time) in the MetaForge twin, with total cost and longest lead time, linked to the BOM it was sourced from. Use when the user asks to raise, record or save a purchase order, buy list, procurement record or order for a build, or to turn a priced BOM into an order the team can review.
domain: supply_chain
---

# create_procurement_record

Write down what is being bought for a build, from whom, at what price and
how long it will take, as a reviewable record in the twin. The record
carries its line items, the total cost (sum of quantity x unit cost), the
longest lead time, and a link back to the BOM it was sourced from. It does
not place an order with anyone.

## When to use it

- "Raise a PO for the Rev A build from the BOM."
- "Record that we are buying 10 of each part from Digi-Key."
- "Save the order for the prototype run so the team can review the cost."

Not for:

- Finding prices or stock: use `distributors.resolve_offers` (see the
  `score_bom_risk` and `find_alternates` skills for the wider sourcing work).
- Choosing or changing a part on the BOM: use
  `twin.record_component_selection`.
- Actually ordering. MetaForge has no ordering tool; say so if asked.

## Tools and profile

| Step | Tool | Profile |
|---|---|---|
| Check the connection | `health.check` | every profile |
| Find the project | `project.open`, `project.get` | every profile |
| Find the BOM and its items | `metaforge://twin/brief/<project_id>` (resource), `twin.find_by_property`, `twin.get_node` | every profile |
| Get current offers (optional) | `distributors.resolve_offers` | `electronics` |
| Record (preferred) | `twin.commit_procurement_record` | not served by the dev MCP server; see below |
| Record (fallback) | `twin.record_document` | `core` |
| Record a sourcing choice | `twin.record_decision` | every profile |

`twin.commit_procurement_record` is listed in the `electronics` profile in
code but is registered only when the server is wired with a
procurement-record recorder. The gateway's in-process harness has it; the
dev MCP sidecar does not, so it is absent even on `electronics`. Expect to
use the fallback, which needs `core`. Pricing and recording therefore sit
on different profiles: price first on `electronics`, then record on `core`,
or work on a connection with no profile (the full tool set).

## Inputs you need before you start

Ask the user for anything missing. Do not fill these in with defaults.

| Input | Why it matters | Example |
|---|---|---|
| Record name | Names the work product | `Quadruped Rev A PO` |
| Project | The record is linked to it | `Quadruped` |
| Source BOM | Links the record to what it was sourced from | a BOM work product node id |
| Per line: part number | What is bought | `STM32F405RGT6` |
| Per line: quantity | Must be greater than 0 | `10` |
| Per line: unit cost | Must be 0 or more; at that quantity's price break | `8.42` |
| Per line: currency | Three-letter code; one currency per record | `USD` |
| Per line: distributor | Who it is bought from | `DigiKey` |
| Per line: lead time in days | Drives the record's longest lead time | `14` |
| Notes | Anything a reviewer needs (build, deadline, approver) | "For 10 prototype units" |

A unit cost or lead time must come from the user or from a distributor
result you show them. If neither has it, ask; do not fill in a typical price
or a lead time of 0.

## Procedure

### 1. Resolve the project and the BOM

1. Call `health.check`; note the active profile.
2. Call `project.open` with the user's words as `query`. Several matches:
   ask which one.
3. Read `metaforge://twin/brief/<project_id>` to find the BOM. To list the
   project's BOM lines, call `twin.find_by_property` with
   `node_type: "BOMItem"`, `property: "project_id"`, `value: <project_id>`.
   Each item carries `part_number`, `manufacturer`, `quantity`,
   `unit_cost` and `supplier` when known.
4. If the user named a source BOM work product, confirm it exists with
   `twin.get_node` (`node_id`). The handler refuses to write when the BOM
   is not found; so should you. Tell the user and ask for the right id.

### 2. Price the lines, if the user wants current prices

On `electronics` (or the full set), call `distributors.resolve_offers` with
`items: [{"mpn": "<part>", "required_qty": <build quantity>}]`, adding
`deadline_days` when the user has a build date. For each result:

- `status: "ok"`: `offers` are ranked; use an offer's
  `unit_price_at_qty`, `currency`, `distributor` and `lead_time_days`.
  Compare offers by `total_committed_cost`, not unit price: MOQ can force a
  larger purchase (`committed_qty` above `required_qty`).
- `insufficient_stock_everywhere`: no distributor has enough. Show the
  user; do not quietly split the order.
- `no_offers_found`: no configured distributor carries it. Ask the user for
  the price and source.
- `currency_mismatch: true`: offers are in different currencies; the
  ranking compared raw numbers. Tell the user.

Show the chosen offer per line and get the user's agreement before using it.
If `committed_qty` is above the build quantity, ask which quantity goes on
the record.

### 3. Check the lines

- Every line has `part_number`, `quantity` > 0 and `unit_cost` >= 0.
- All lines share one currency. Mixed currencies are refused by the
  recorder; split into one record per currency, with the user's agreement.
- Compute `total_cost` = sum of quantity x unit cost, and
  `max_lead_time_days` = the largest lead time. Show both.

### 4. Record it

Ask the user before writing. Then use the first path your list allows.

**a. If `twin.commit_procurement_record` is in your tool list**, call it
with `name`, `line_items` (each `{part_number, description, quantity,
unit_cost, currency, distributor, lead_time_days}`), `notes`,
`source_node_ids: ["<BOM node id>"]` and `project_id`. It returns
`node_id`, `line_item_count`, `total_cost`, `currency` and
`max_lead_time_days`; check they match yours.

**b. If it is not**, call `twin.record_document` (profile `core`) with:

- `document_type: "documentation"`
- `name`: the record name
- `content`: markdown with `# Procurement Record: <name>`, a line
  `Total cost: <total> <currency>, max lead time: <days> days`, a table with
  columns Part Number, Description, Qty, Unit Cost, Line Total,
  Distributor, Lead Time (days), and a Notes section if there are notes
- `metadata`: `line_item_count`, `total_cost`, `currency`,
  `max_lead_time_days`, `line_items`, `notes`
- `source_part_node_ids: ["<BOM node id>"]` to link it to the BOM
- `project_id`

Tell the user it was stored as a documentation work product, not a typed
PROCUREMENT_RECORD, because this connection does not serve that recorder.

If the user chose one distributor over a cheaper or faster one for a
reason (stock, trust, consolidation), record it with `twin.record_decision`
including the alternative.

### 5. Report

- Node id, name, which recording path was used.
- Line count, total cost with currency, longest lead time and its part.
- Lines with no distributor offer, insufficient stock or MOQ overbuy.
- That no order was placed.

## Checks before you report

- [ ] Every price and lead time came from the user or a shown distributor result
- [ ] One currency per record
- [ ] Source BOM confirmed to exist, or the user chose to record without it
- [ ] Totals recomputed and matching the server's
- [ ] You said whether the record is typed or a documentation fallback

## Failure handling

| Symptom | Likely cause | What to do |
|---|---|---|
| `twin.commit_procurement_record` not in your list | Server not wired with that recorder (normal on the dev sidecar) | Use path 4b; tell the user. |
| `distributors.resolve_offers` not in your list | Not on `electronics` or the full set | Ask the user for prices, or to reconnect with `?profile=electronics`. |
| `twin.record_document` not in your list | Not on `core` | Price here, then record on a `core` connection; say so. |
| "mixed currencies ... not supported" | Lines in more than one currency | Split into one record per currency. |
| BOM node not found | Wrong id, or a different project | Ask the user; do not record against a guessed BOM. |
| `no_offers_found` for a line | No configured distributor carries it | Ask the user for price and source. |
| A write is held / `approval_required` | Writes need a person on this connection | Tell the user where it waits; do not retry or reword it. |
| `-32001` naming an adapter | That adapter's container is down | Report it; distributor tools may also return empty when credentials are missing. |

## Limits

- A record, not an order: nothing is sent to a distributor.
- One currency per record; no exchange-rate conversion anywhere.
- Not idempotent: every call creates a new node; revise by recording a new
  one and saying which supersedes which.
- Prices and stock are a snapshot from when you fetched them.
