# Tier-2 — tool versioning compatibility probe (Claude-driven, weekly)

Validates: MET-408. The MCP tool versioning convention (MET-389)
holds when a real Claude session enumerates and calls tools.
Tier: 2 (weekly probe)
Run: `/uat-cycle12 --tier 2 --scenario versioning`

---

## What the validator checks (rolled up across scenarios)

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

## Scenario: alias resolves to the latest registered version
Validates: MET-408, MET-389
Tier: 2

### Given
- The MCP server registers `knowledge.search` with at least one
  versioned form (`knowledge.search@v1`) and exposes the
  unversioned alias `knowledge.search` resolving to the latest
  version per the MET-389 convention.
- The Claude session has performed `tool/list` and seen both
  the alias and versioned names.

### When
1. Prompt Claude with a question that naturally requires
   knowledge retrieval, e.g. *"Search the knowledge base for
   thermal cycling failures."*
2. Capture the tool-call frame Claude emits and the matching
   span in the trace.

### Then
- Claude calls the alias `knowledge.search` (no `@vN` suffix in
  the tool-call name field).
- The server-side span resolves the alias to the latest
  registered version (`knowledge.search@v1` while only v1 is
  registered).
- The call returns hits without a `tool_not_found` or
  `invalid_tool_name` error.

---

## Scenario: explicit version pinning still works
Validates: MET-408, MET-389
Tier: 2

### Given
- `.mcp.json` (or the equivalent registration) is pinned so
  that only the explicitly-versioned form
  `knowledge.search@v1` is advertised in `tool/list` for this
  session — no bare alias.
- A fresh Claude session has reloaded its tool list against
  this pinned configuration.

### When
1. Prompt Claude with a question that requires knowledge
   retrieval.
2. Capture the tool-call name Claude emits.

### Then
- Claude calls the tool using the versioned name
  `knowledge.search@v1`.
- The call succeeds — no `tool_not_found` error, no
  transport-level crash.
- Claude does not silently fall back to a different tool to
  hide an error.

---

## Scenario: v1 and v2 coexist in one session
Validates: MET-408, MET-389
Tier: 2

### Given
- Both `knowledge.search@v1` and `knowledge.search@v2` are
  mock-registered with intentionally different output shapes
  (e.g. v2 adds a `citations` field that v1 lacks).
- Both tool descriptions are visible to Claude in `tool/list`,
  and the alias `knowledge.search` resolves to the latest
  (`@v2`).

### When
1. Prompt Claude with a question whose ideal answer requires
   the v2-only `citations` field, e.g. *"Search for thermal
   failures and include citations."*
2. Prompt Claude with a question whose v1-shape output is
   sufficient, e.g. *"Just tell me if there are any thermal
   failure hits — no citations needed."*
3. Capture the tool-call name(s) Claude emits across both
   prompts in the same session.

### Then
- Claude picks `knowledge.search@v2` (or the alias resolving
  to v2) for the citations-bearing prompt and accepts the
  richer output shape.
- Both versioned tools remain callable in the same session —
  no tool is implicitly disabled by the registry just because
  another version exists.
- Neither call returns `tool_not_found`; both return
  well-formed tool responses.

---

## Scenario: deprecation surfaces in Claude's behavior
Validates: MET-408, MET-389
Tier: 2

### Given
- `knowledge.search@v1` is marked deprecated in its tool
  description (e.g. the description begins with `[DEPRECATED]`
  or carries a `deprecated: true` field per the MET-389
  convention).
- `knowledge.search@v2` is registered as non-deprecated, and
  the alias `knowledge.search` resolves to v2.
- The Claude session has reloaded its tool list against this
  configuration.

### When
1. Prompt Claude with a question that requires knowledge
   search but does not pin a version, e.g. *"Search for
   thermal cycling failures."*
2. In a follow-up turn, explicitly instruct Claude to use the
   deprecated form: *"Use `knowledge.search@v1` specifically
   for this next query."*

### Then
- For step 1, Claude prefers the non-deprecated alias (the
  tool-call name is `knowledge.search` and resolves
  server-side to `@v2`); it does not pick the deprecated
  versioned name.
- For step 2, when the user explicitly pins to the deprecated
  form, Claude calls `knowledge.search@v1` as instructed but
  surfaces the deprecation note in its user-facing reply (e.g.
  mentions that v1 is deprecated and v2 is the current
  recommended form).
- Neither call fails with `tool_not_found` — deprecation does
  not retire the tool yet.

---

## Scenario: clean retirement yields tool_not_found, not a crash
Validates: MET-408, MET-389, MET-385
Tier: 2

### Given
- The documented grace period for `knowledge.search@v1` has
  elapsed; the registry no longer advertises `@v1` in
  `tool/list`. Only `knowledge.search@v2` (and the alias
  resolving to it) remain.
- A test harness can issue an explicit pinned call to
  `knowledge.search@v1` even though it is no longer in the
  tool list (bypassing Claude's tool-list filter).

### When
1. Issue an explicit pinned call to `knowledge.search@v1` from
   the harness.
2. In the same Claude session, ask Claude (who sees the
   refreshed tool list without `@v1`) to perform a knowledge
   search and report on the retirement state.

### Then
- Step 1 returns a structured error matching the MET-385
  envelope: `code == "tool_not_found"`, with a `message` that
  references the retired tool name literally.
- Step 1 is **not** a transport-level crash — the client
  receives a well-formed JSON-RPC error frame, not a socket
  reset or truncated stream — and the error is **not** a
  generic `internal` code.
- For step 2, Claude reports the retirement honestly in its
  user-facing reply (does not pretend `@v1` still works) and
  uses the alias / `@v2` for any new search call it makes.

---

## Acceptance

- Probe spec written and runnable from `/uat-cycle12 --tier 2
  --scenario versioning`.
- All 5 scenarios PASS.
- Report committed.
- Any drift from the MET-389 convention → Linear bug filed.
