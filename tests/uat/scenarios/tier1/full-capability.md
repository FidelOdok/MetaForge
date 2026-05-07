# Tier-1 — full-capability gap-fill scenarios

Validates: MET-410 (sub-deliverable F1a of MET-409).
Tier: 1
Run: `/uat-cycle12 --tier 1 --scenario full-capability`

Seven scenarios that close the highest-value tier-1 gaps in the
knowledge-base capability matrix (`docs/uat/kb-test-plan.md`). Each
scenario is the executable promotion of a 🔄 NEW catalog row from
§1 / §2 / §5 / §7 / §10 of the master plan. Four of them already
have backing implementations from prior L1 PRs (L1-A1 project
isolation, L1-A6 supersede-on-edit, L1-B1 resources surface) and
should record ✅ PASS on the next cycle run; three (KB-VER-003
staleness threshold, KB-ING-012 UTF-8 round-trip, KB-SRC-011
post-edit search) report SKIP / BLOCKED until their backing
implementations land.

Every scenario assumes the MetaForge MCP server is connected and
both `mcp__metaforge__knowledge_ingest` and
`mcp__metaforge__knowledge_search` are listed in `tools/list`.

> **Note:** Scenarios deliberately use unique source paths under
> `uat://kb/full-cap/<id-suffix>` so they do not collide with
> tier1 fixtures owned by `knowledge.md` or `ingest.md`.

---

## Scenario: KB-ING-009 — re-ingest after edit retires stale fragments
Validates: MET-307
Tier: 1

### Given
- Source path `uat://kb/full-cap/ing-009-edit`.
- Two distinct content payloads, same path:
  - payload α = `"alpha-marker-A1 — full-capability ing-009 stale"`.
  - payload β = `"beta-marker-B1 — full-capability ing-009 fresh"`.

### When
1. Ingest payload α at `uat://kb/full-cap/ing-009-edit` with
   `knowledge_type="design_decision"`.
2. Ingest payload β at the **same** source path with the same
   `knowledge_type`.
3. Search for `"alpha-marker-A1"` with `top_k=10`.
4. Search for `"beta-marker-B1"` with `top_k=10`.

### Then
- Step 3 returns 0 hits, OR every hit it returns has
  `similarity_score < 0.5` (stale α retired or below threshold).
- Step 4 returns ≥ 1 hit whose `source_path ==
  "uat://kb/full-cap/ing-009-edit"`.
- The total chunk count visible at
  `metaforge://knowledge/sources/{id}` for that source equals the
  chunk count produced by payload β alone (no leftover α
  fragments).

---

## Scenario: KB-ING-012 — ingest preserves UTF-8 / non-ASCII content
Validates: MET-346
Tier: 1

### Given
- Source path `uat://kb/full-cap/ing-012-utf8`.
- Content with mixed CJK + Latin diacritics + math symbols:
  `"焊接质量 — Schweißnahtqualität — joint α₁ ≈ 0.7"`.

### When
1. Ingest the content above with
   `knowledge_type="design_decision"`.
2. Search for `"Schweißnahtqualität"` with `top_k=1`.

### Then
- Step 1 returns `chunks_indexed >= 1`.
- Step 2 returns ≥ 1 hit at the source path.
- The hit's `content` round-trips the diacritics and CJK glyphs
  byte-for-byte (`焊接质量`, `Schweißnahtqualität`, `α₁`, `≈ 0.7`
  all present unchanged — no `?`, mojibake, or NFC/NFD drift).

---

## Scenario: KB-CTX-002 — project isolation between ingest and search
Validates: MET-401
Tier: 1

### Given
- Two distinct project UUIDs `P_A` and `P_B` (any two unequal v4
  UUIDs the runner generates fresh — e.g.
  `P_A = "11111111-1111-4111-8111-111111111111"`,
  `P_B = "22222222-2222-4222-8222-222222222222"`).
- Source path `uat://kb/full-cap/ctx-002-iso`.
- Marker token `"isolation-marker-X42"`.

### When
1. Under per-call context `{project_id: P_A}`, ingest the marker
   content at the source path with
   `knowledge_type="design_decision"`.
2. Under per-call context `{project_id: P_A}`, search for
   `"isolation-marker-X42"` with `top_k=5`.
3. Under per-call context `{project_id: P_B}`, search for the
   **same** query with `top_k=5`.

### Then
- Step 2 returns ≥ 1 hit whose `source_path` matches the ingest
  source path.
- Step 3 returns 0 hits (or only hits whose
  `similarity_score < 0.5`) — no leakage from project A into
  project B.

---

## Scenario: KB-RES-002 — single-source detail via `sources/{id}`
Validates: MET-384
Tier: 1

### Given
- Source path `uat://kb/full-cap/res-002-detail`.
- Content `"resource-detail probe — single source view via id"`.

### When
1. Ingest the content with
   `knowledge_type="design_decision"`.
2. Read `metaforge://knowledge/sources` via MCP `resources/read`
   and capture the `id` of the entry whose `source_path` matches
   the ingest path.
3. Read `metaforge://knowledge/sources/{id}` via MCP
   `resources/read` using the captured id.

### Then
- Step 2 returns a list containing the just-ingested source with a
  non-empty `id`, non-empty `indexedAt`, and `fragmentCount >= 1`.
- Step 3 returns a single-source object that includes:
  - the source's metadata (at minimum `source_path`, `indexedAt`,
    `fragmentCount`);
  - a chunk list (length equals `fragmentCount`);
  - a content preview or per-chunk `content` string.
- Step 3's response schema is stable — no required field is
  missing and no undeclared extra fields appear at the top level.

---

## Scenario: KB-RES-003 — `resources/list` advertises knowledge URIs
Validates: MET-384
Tier: 1

### Given
- (none — purely tests capability advertisement)

### When
1. At session start (or any time before the first ingest), call
   MCP `resources/list`.

### Then
- The response includes at least the
  `metaforge://knowledge/sources` URI in its advertised resources.
- That URI entry has a non-empty `name` and non-empty
  `description` (the runner does not need to assert exact strings —
  only that the fields are present and non-empty).

---

## Scenario: KB-VER-003 — staleness-threshold filter excludes old hits
Validates: MET-323
Tier: 1

### Given
- A staleness-threshold filter on `knowledge_search` that accepts
  `filters={"max_age_days": <int>}`. **If this filter is not yet
  wired in the running gateway, the scenario reports BLOCKED
  rather than FAIL.**
- Two ingested documents that share a unique phrase
  `"shared-staleness-token-Q9"`:
  - one whose `metadata.indexed_at` is **at least 30 days** in the
    past (source path `uat://kb/full-cap/ver-003-old`);
  - one whose `indexed_at` is fresh / within the last day (source
    path `uat://kb/full-cap/ver-003-fresh`).

### When
1. Ingest the old document with explicit
   `metadata.indexed_at` set to a timestamp ≥ 30 days ago.
2. Ingest the fresh document with no override (server-side
   `indexed_at` = now).
3. `knowledge_search(query="shared-staleness-token-Q9", top_k=5,
   filters={"max_age_days": 14})`.

### Then
- Step 3 returns only the fresh hit
  (`source_path == "uat://kb/full-cap/ver-003-fresh"`).
- The old document's `source_path` does **not** appear in any
  returned hit.
- If the gateway rejects `max_age_days` as an unknown filter and
  silently returns both hits (per KB-SRC-014 pinned behaviour),
  the scenario records **BLOCKED** with the message
  `"max_age_days filter not yet wired"` — not FAIL.

---

## Scenario: KB-SRC-011 — search returns hit even after re-edit
Validates: MET-307, MET-346
Tier: 1

### Given
- KB-ING-009 has just executed in the same `/uat-cycle12`
  invocation (the supersede-on-edit pair). Source path is
  `uat://kb/full-cap/ing-009-edit`. Payload β is the fresh
  content; payload α is the retired content.

### When
1. `knowledge_search(query="alpha-marker-A1", top_k=5)` (stale
   phrase from retired payload α).
2. `knowledge_search(query="beta-marker-B1", top_k=5)` (fresh
   phrase from current payload β).

### Then
- Step 1 returns 0 hits, OR every hit has
  `similarity_score < 0.5` (stale phrase suppressed).
- Step 2 returns ≥ 1 hit whose `source_path ==
  "uat://kb/full-cap/ing-009-edit"`.
- Step 2's top hit's `content` contains the literal phrase
  `"beta-marker-B1"`.
