# Tier-1 — `constraint.*` scenarios

Validates Cycle 3 / MET-383 — the constraint engine MCP tool —
from a Claude-as-real-user perspective. Run on Cycle gates.

Every scenario assumes the MetaForge MCP server is connected, a
`ConstraintEngine` is wired into bootstrap, and `constraint.validate`
appears in `tool/list`.

---

## Scenario: tool/list reports constraint.validate
Validates: MET-383 (registration)
Tier: 1

### Given
- A bootstrapped MCP server with the constraint adapter enabled.

### When
1. Call `tool/list` (no filter).

### Then
- The result includes a manifest for `constraint.validate`.
- Manifest has `adapter_id == "constraint"` and a description
  whose phrasing makes the tool pickable by an LLM ("validate",
  "check constraints", "evaluate rules" — pattern, not literal).

---

## Scenario: validate clean — empty violations on a passing project
Validates: MET-383 (passing path)
Tier: 1

### Given
- A project with no triggering constraint conditions (or an
  empty work-product set).

### When
1. Call `constraint.validate` with `{ "work_product_ids": [] }`.

### Then
- Call returns successfully.
- `data.passed == true`.
- `data.evaluated_count >= 0`.
- The violations array (if present) is empty.

---

## Scenario: validate flags a power-budget overrun
Validates: MET-383 (single-violation path)
Tier: 1

### Given
- A power-budget rule loaded into the engine.
- A work product whose `power_draw_mw` exceeds the budget.

### When
1. Call `constraint.validate` with the offending
   `work_product_ids: [<wp_id>]`.

### Then
- `data.passed == false`.
- The violations array has length >= 1.
- At least one violation has `severity == "error"` and a
  non-empty `message`.
- `rule_id` (or equivalent) is set so the harness can route
  to the source rule.

---

## Scenario: multi-rule validation aggregates results
Validates: MET-383 (aggregation)
Tier: 1

### Given
- Three rules loaded: power budget, thermal margin, BOM
  authority. A work-product set that violates the first two.

### When
1. Call `constraint.validate` with `{ "work_product_ids":
   [<wp_set>] }`.

### Then
- `data.passed == false`.
- The violations cover at least both expected rule_ids — no
  silent merge, no dropped violation.
- Each violation has `affected_nodes` (or equivalent) listing
  which work-products triggered it.

---

## Scenario: severity scale is honoured (error > warning > info)
Validates: MET-383 (severity wire shape)
Tier: 1

### Given
- A rule loaded at each severity (one `error`, one `warning`,
  one `info`) and a work-product set that triggers all three.

### When
1. Call `constraint.validate` with the work-product set.

### Then
- `data.passed == false` because at least one `error` violation
  exists.
- All three severities appear in the violations list.
- Severities use exactly `"error"` / `"warning"` / `"info"`
  (lowercase) — case-stable wire format.

---

## Scenario: malformed input returns a structured error
Validates: MET-383 (input validation)
Tier: 1

### Given
- A non-UUID string in `work_product_ids`.

### When
1. Call `constraint.validate` with `{ "work_product_ids":
   ["not-a-uuid"] }`.

### Then
- The JSON-RPC response carries an `error` (no `result`).
- The error envelope follows the MET-385 standardised contract:
  carries `code`, `message`, and (for INVALID_INPUT) is
  `retryable: false`.
