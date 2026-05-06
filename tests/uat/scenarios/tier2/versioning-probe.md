# Tier-2 — tool versioning compatibility probe (Claude-driven, weekly)

Validates: MET-408. The MCP tool versioning convention (MET-389)
holds when a real Claude session enumerates and calls tools.
Tier: 2 (weekly probe)
Run: `/uat-cycle12 --tier 2 --scenario versioning`

---

## Scenarios

### Scenario 1 — alias resolution
Give Claude a prompt that requires `knowledge.search`. Watch the
trace.

**Pass:** Claude calls the alias `knowledge.search` (which
resolves to the latest registered version) — not the explicit
`knowledge.search@v1`.

### Scenario 2 — explicit version pinning
Pin the `.mcp.json` to register only `knowledge.search@v1`.
Claude's `tool/list` now shows the versioned name.

**Pass:** Claude still calls the tool correctly using the
versioned name. No tool-not-found error.

### Scenario 3 — coexistence (v1 + v2)
Mock-register both `knowledge.search@v1` and
`knowledge.search@v2` (different output shapes). Claude reads
both descriptions.

**Pass:** Claude picks the right version based on the question
shape; both versions remain callable in the same session.

### Scenario 4 — deprecation handling
Mark `knowledge.search@v1` as deprecated in its description
(while v2 is non-deprecated and the alias still resolves to v2).
Ask Claude to search.

**Pass:** Claude prefers the non-deprecated alias when available.
If the user pins to the deprecated form explicitly, Claude
relays the deprecation note in its reply.

### Scenario 5 — clean retirement
After the documented grace period, retire `@v1` from the
registry. Pin a harness to call `knowledge.search@v1` explicitly.

**Pass:** Clean failure with `code="tool_not_found"` (per the
MET-385 envelope), not a generic `internal` error. Claude
reports the retirement honestly.

---

## What the validator checks

- `tool/list` response includes both the alias form
  (`knowledge.search`) and the versioned form
  (`knowledge.search@v1`) for every tool.
- Adding a hypothetical `@v2` keeps `@v1` working until
  retirement.
- Removing `@v1` after the grace period yields a clean
  `tool_not_found` error, not a transport crash.
- Documentation around the migration recipe is reachable
  (MET-389 doc anchor).

---

## Acceptance

- Probe spec written and runnable from `/uat-cycle12 --tier 2
  --scenario versioning`.
- All 5 scenarios PASS.
- Report committed.
- Any drift from the MET-389 convention → Linear bug filed.
