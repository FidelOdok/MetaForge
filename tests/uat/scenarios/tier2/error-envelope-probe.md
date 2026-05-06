# Tier-2 — error envelope conformance probe (Claude-driven, weekly)

Validates: MET-406. Conformance to the standardized
`McpToolError` envelope (MET-385) **and** Claude's user-facing
behavior when each error code is triggered.
Tier: 2 (weekly probe)
Run: `/uat-cycle12 --tier 2 --scenario error-envelope`

---

## Why Claude-driven (not synthetic)

A synthetic test triggers errors and asserts response shape.
Claude-driven goes further: when an error occurs, does Claude do
the right thing in response? This probe checks both layers — the
envelope conformance **and** the harness-level behavior.

---

## Probe matrix

For each error code below, prompt Claude in a way that naturally
triggers it, then grade two things:

1. **Envelope shape** — valid `McpToolError` with the expected
   `code`, a `retryable` boolean, and (when OTel is on) a
   `trace_id`.
2. **Harness behavior** — Claude's user-facing reply is honest
   and actionable; retries follow the `retryable` flag.

---

### 1. INVALID_INPUT
- **Trigger:** *"Find a BOMItem with MPN 12345"* (numeric MPN
  where a string is required).
- **Envelope:** `code="invalid_input"`, `retryable=false`.
- **Behavior:** Claude either auto-corrects to a string and
  retries, or reports the validation error clearly with the
  field name.

### 2. NOT_FOUND
- **Trigger:** *"Get the node with id `does-not-exist`."*
- **Envelope:** `code="not_found"`, `retryable=false`.
- **Behavior:** Claude reports "not found" honestly. No infinite
  retry loop.

### 3. CONFLICT
- **Trigger:** Pre-stale a node (modify it via direct API), then
  ask Claude to update it through a versioned tool.
- **Envelope:** `code="conflict"`, `retryable=true` (after
  refresh).
- **Behavior:** Claude detects the version mismatch and either
  re-reads + retries, or explains the conflict to the user.

### 4. CONSTRAINT_VIOLATION
- **Trigger:** *"Add a 500mA load to a 1A-budget rail that's
  already at 0.8A."*
- **Envelope:** `code="constraint_violation"`, `retryable=false`,
  `details` includes severity + remediation.
- **Behavior:** Claude surfaces the violation with its severity
  and suggested remediation; does not silently commit.

### 5. BACKEND_UNAVAILABLE
- **Trigger:** Stop the Postgres container, ask Claude to search.
- **Envelope:** `code="backend_unavailable"`, `retryable=true`.
- **Behavior:** Claude suggests checking infra; does not pretend
  the search succeeded; retries within a reasonable bound, then
  surfaces the failure.

### 6. TIMEOUT
- **Trigger:** A deliberately slow path with a short timeout
  (e.g. a large FEA solve with a 1-second cap).
- **Envelope:** `code="timeout"`, `retryable=true`.
- **Behavior:** Claude either waits within the timeout window or
  reports the timeout cleanly with no silent hang.

### 7. AUTH_REQUIRED
- **Trigger:** Misconfigured `.mcp.json` with a wrong API key.
- **Envelope:** `code="auth_required"`, `retryable=false`.
- **Behavior:** Connection is rejected at handshake. Claude
  reports the auth problem; does not retry pointlessly.

### 8. PERMISSION_DENIED
- **Trigger:** Ask Claude to mutate the graph via
  `twin.query_cypher` (read-only path).
- **Envelope:** `code="permission_denied"`, `retryable=false`.
- **Behavior:** Claude either refuses up-front based on tool
  description, or relays the denial cleanly.

### 9. RATE_LIMITED
- **Trigger:** Hammer `knowledge.ingest` with rapid-fire calls
  beyond the rate limit.
- **Envelope:** `code="rate_limited"`, `retryable=true`,
  `details.retry_after_ms` populated.
- **Behavior:** Claude backs off. No retry-spam.

### 10. INTERNAL
- **Trigger:** Mock-injected (hardest to trigger naturally — use
  a feature flag or a fault-injection hook).
- **Envelope:** `code="internal"`, `retryable=false`.
- **Behavior:** Claude reports the failure honestly. Does not
  claim success when none occurred.

---

## What the validator checks

For each error code triggered:

- The response is a valid `McpToolError` shape (10-code enum,
  `retryable` boolean, `trace_id` when OTel is on).
- Claude's user-facing reply is honest (not "everything is fine")
  and actionable (suggests the next step).
- Retryable errors → Claude retries appropriately (bounded).
- Non-retryable errors → Claude does not retry pointlessly.

---

## Acceptance

- All 10 codes triggered and graded.
- All envelopes valid.
- All Claude responses pass the honest+actionable bar.
- Report committed at
  `docs/uat/uat-claude-driven-report-<date>.md`.
- Any non-conforming tool → Linear bug filed.
