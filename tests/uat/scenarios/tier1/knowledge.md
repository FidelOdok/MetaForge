# Tier-1 — `knowledge.*` scenarios

Validates Cycle 1 L1 retrieval from a Claude-as-real-user
perspective. Run on Cycle gates.

Every scenario assumes the MetaForge MCP server is connected and
`knowledge.search` + `knowledge.ingest` are listed in `tool/list`.

---

## Scenario: ingest then search round-trip
Validates: MET-346, MET-293
Tier: 1

### Given
- A unique source_path that no prior ingest has used:
  `"uat://tier1/knowledge/round-trip"`.

### When
1. Ingest content "MetaForge tier-1 marker: dependable persistence
   layer using Postgres + pgvector under the LightRAG adapter."
   with the source_path above and knowledge_type
   `"design_decision"`.
2. Search for `"pgvector LightRAG persistence"` with top_k=5.

### Then
- Step 1 returns `chunks_indexed >= 1`.
- Step 2 returns ≥1 hit whose `source_path` matches the ingest
  source_path.
- The matching hit's `content` contains either "pgvector" or
  "LightRAG".

---

## Scenario: ingest classifies by knowledge_type
Validates: MET-346, MET-307
Tier: 1

### Given
- Two distinct documents to ingest under different types.

### When
1. Ingest content "Failure: thermal cycling broke the aluminium
   6061 mount" at `uat://tier1/knowledge/failure-mode` with
   `knowledge_type: "failure"`.
2. Ingest content "Component: titanium grade 5 sheet, 2mm thick"
   at `uat://tier1/knowledge/component` with `knowledge_type:
   "component"`.
3. Search for `"titanium"` with top_k=5 and a knowledge_type
   filter of `"component"`.

### Then
- Step 3 returns at least one hit.
- Every returned hit has `knowledge_type == "component"` (no
  failure-typed leaks). If the field is `null` on a hit, that's
  acceptable — but no hit should report `"failure"`.

---

## Scenario: empty search produces deterministic empty list
Validates: MET-346
Tier: 1

### Given
- A query string that's known to have no relevant content
  (e.g. `"xyz-uat-marker-no-match-zzzzzzz"`).

### When
1. Search for that string with top_k=3.

### Then
- The response is a syntactically valid hits list.
- The list has 0 hits OR all returned hits have similarity_score
  below 0.5 (low-confidence). Either is an acceptable
  "no-match" signal — we just want determinism, not a crash.

---

## Scenario: search respects top_k cap
Validates: MET-293
Tier: 1

### Given
- At least 3 documents already ingested (use the round-trip and
  classification scenarios as setup; running them earlier in the
  same /uat-cycle12 invocation is the simplest way).

### When
1. Search for `"MetaForge"` (broad query, should hit multiple) with
   top_k=2.

### Then
- The response contains at most 2 hits — the cap is respected.

---

## Scenario: knowledge.search response carries citation fields
Validates: MET-293, MET-335
Tier: 1

### Given
- One ingested document at a known source_path.

### When
1. Ingest content "Citation field probe — heading 'API' / chunk 0."
   at `uat://tier1/knowledge/citation`, knowledge_type
   `"design_decision"`.
2. Search for `"citation field probe"` with top_k=1.

### Then
- The single hit has a non-empty `source_path`.
- The hit object exposes `chunk_index` and `total_chunks` fields
  (may be 0 / 1 for a single-chunk doc — they must not be missing).

---

## Scenario: knowledge.ingest rejects empty content cleanly
Validates: MET-346
Tier: 1

### Given
- (none — purely tests error handling)

### When
1. Call `knowledge.ingest` with `content=""` and any source_path.

### Then
- Either:
  (a) the call returns `status: "failure"` with a message
       mentioning empty content, OR
  (b) the call raises a tool execution error (-32001) whose data
      payload mentions empty/blank content.
- It must NOT silently succeed with `chunks_indexed=0` — that's
  the failure mode this scenario exists to detect.

---

## Scenario: deduplication on identical re-ingest
Validates: MET-307, MET-346
Tier: 1

### Given
- A unique source_path: `"uat://tier1/knowledge/dedup-probe"`.

### When
1. Ingest content "Dedup probe content unique-token-q9z." at the
   source_path above.
2. Ingest the **identical** content at the **same** source_path.
3. Search for `"unique-token-q9z"` with top_k=10.

### Then
- Step 3 returns one hit (de-duplicated), not two.
- The single hit's source_path matches the ingest source_path.

---

## Scenario: forge ingest equivalent — directory walk
Validates: MET-336
Tier: 1

### Given
- (no MCP-direct equivalent of the CLI walker today; this scenario
  validates the contract by ingesting two markdown-shaped strings
  and confirming both surface)

### When
1. Ingest content "Walker file 1 — covers the L0 persistence layer."
   at `uat://tier1/knowledge/walker/file-1.md`, knowledge_type
   `"design_decision"`.
2. Ingest content "Walker file 2 — covers the L1 retrieval layer."
   at `uat://tier1/knowledge/walker/file-2.md`, knowledge_type
   `"design_decision"`.
3. Search for `"persistence retrieval layer"` with top_k=5.

### Then
- Both source_paths appear in the top 5 hits.
