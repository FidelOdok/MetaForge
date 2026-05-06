# Tier-1 — `constraint.validate` happy path (HP-CONS)

Validates: MET-413 (sub-deliverable 4/10 of MET-409).
Tier: 1
Run: `/uat-cycle12 --tier 1 --scenario constraint-hp`

Ten happy-path scenarios for the constraint engine MCP tool
(MET-383). Every scenario assumes `constraint.validate` is
listed in `tool/list`.

> **Note:** Several scenarios depend on seeded constraint rules
> (power, thermal, requirement coverage). If the rule pack is
> missing the scenario reports BLOCKED, not FAIL.

---

## Scenario: HP-CONS-01 — clean project returns empty violations
Validates: MET-383
Tier: 1

### Given
- A project with no rule breaches in any domain.

### When
1. Call `constraint.validate` for the project.

### Then
- `violations=[]`. No error, no null.

---

## Scenario: HP-CONS-02 — single violation surfaced with severity
Validates: MET-383
Tier: 1

### Given
- A project with one known power-budget breach (load > rail
  capacity).

### When
1. Call `constraint.validate`.

### Then
- One violation returned with `severity="error"`, a non-empty
  `message`, and a `remediation` string suggesting how to fix.
- The violation references the affected node IDs.

---

## Scenario: HP-CONS-03 — multi-violation severity ordering preserved
Validates: MET-383
Tier: 1

### Given
- A project seeded with one `error`, one `warning`, one `info`.

### When
1. Call `constraint.validate`.

### Then
- The result array is ordered: error first, then warning, then
  info. Within a bucket, ordering is stable but unspecified.

---

## Scenario: HP-CONS-04 — power budget rule fires correctly
Validates: MET-383 (power rule)
Tier: 1

### Given
- A 1A rail with a 0.6A baseline load. Add a 0.5A load to
  exceed the budget.

### When
1. Call `constraint.validate`.

### Then
- A `power_budget` violation appears.
- `details` cites both the actual load (1.1A) and the rail
  limit (1A).

---

## Scenario: HP-CONS-05 — thermal margin rule fires
Validates: MET-383 (thermal rule)
Tier: 1

### Given
- A component configured close to its junction-temperature limit
  (within the configured margin).

### When
1. Call `constraint.validate`.

### Then
- A `thermal_margin` violation appears.
- `details` includes the actual junction temperature, the limit,
  and the delta.

---

## Scenario: HP-CONS-06 — requirement coverage rule fires
Validates: MET-383 (coverage rule)
Tier: 1

### Given
- A Requirement node with no linked TestProcedure or Evidence.

### When
1. Call `constraint.validate`.

### Then
- A `requirement_coverage` violation appears flagging the
  unverified Requirement and the missing TestProcedure linkage.

---

## Scenario: HP-CONS-07 — cross-domain rule fires
Validates: MET-383 (cross-domain composition)
Tier: 1

### Given
- A connector configured with mismatched mechanical mating
  (electrical pin ↔ mechanical alignment violation).

### When
1. Call `constraint.validate`.

### Then
- One violation surfaces that references both the electrical
  Constraint and the mechanical Constraint (composite rule).

---

## Scenario: HP-CONS-08 — pre-flight validation (proposed change)
Validates: MET-383 (proposal mode)
Tier: 1

### Given
- A clean project; a candidate `BOMItem` change in hand.

### When
1. Call `constraint.validate` with the proposed change supplied as
   `proposed_changes` (no commit).

### Then
- Any violations the change would cause are returned.
- The graph is unchanged (re-querying counts confirms zero new
  nodes/edges were committed).

---

## Scenario: HP-CONS-09 — performance bound under 500ms
Validates: MET-383
Tier: 1

### Given
- A seeded project with ≥ 1000 nodes and ≥ 50 active rules.

### When
1. Run `constraint.validate` 20 times back-to-back.

### Then
- p95 latency < 500ms on standard dev hardware. Hardware-
  constrained dev rigs may report BLOCKED with environment notes.

---

## Scenario: HP-CONS-10 — severity gating semantics
Validates: MET-383 (severity contract)
Tier: 1

### Given
- A project with one `error` and one `info` violation.

### When
1. Call `constraint.validate`.

### Then
- The reviewer/Claude can clearly distinguish blocking from
  informational severity from the response shape alone (severity
  field present and consistent with documented enum).
- Documentation or response metadata signals that `error`
  blocks gate transitions while `info` does not.

---

## Acceptance

- All 10 scenarios PASS.
- Report committed.
