# Tier-1 — knowledge retrieval happy path (HP-RETR)

Validates: MET-411 (sub-deliverable 2/10 of MET-409).
Tier: 1
Run: `/uat-cycle12 --tier 1 --scenario retrieval`

Ten happy-path scenarios for `knowledge.search` from a Claude-as-
real-user perspective. Every scenario assumes
`knowledge.ingest` has run at least once in the session (use the
HP-INGEST scenarios as setup, or rely on a pre-seeded corpus).

---

## Scenario: HP-RETR-01 — top-5 search returns 5 ordered hits
Validates: MET-293 (top_k cap), MET-335 (similarity score)
Tier: 1

### Given
- A corpus containing several documents that mention "thermal
  management" (use any seeded fixtures or the HP-INGEST setup).

### When
1. Call `knowledge.search` with `query="thermal management"`,
   `top_k=5`.

### Then
- Exactly 5 hits returned (or, if the corpus has fewer matches,
  the maximum available — never more than 5).
- `similarity_score` decreases monotonically across the result
  array (top hit is the strongest match).

---

## Scenario: HP-RETR-02 — knowledge_type filter narrows results
Validates: MET-307, MET-346
Tier: 1

### Given
- The corpus contains both `design_decision` and `failure` chunks
  about MCU selection.

### When
1. Search `query="MCU selection"`, `top_k=10`, filter
   `knowledge_type="design_decision"`.

### Then
- Every returned hit has `knowledge_type="design_decision"`.
- No hits with `knowledge_type="failure"` leak through.

---

## Scenario: HP-RETR-03 — similarity threshold drops low-quality hits
Validates: MET-335 (threshold)
Tier: 1

### Given
- Mixed-quality chunks (some highly relevant, some marginal).

### When
1. Search `query="STM32H7"` with a high relevance threshold
   (e.g. `min_similarity=0.7`).

### Then
- Every returned hit has `similarity_score >= 0.7`.
- The total result count is ≤ the unfiltered baseline.

---

## Scenario: HP-RETR-04 — citation fields populated end-to-end
Validates: MET-335 (citation enrichment)
Tier: 1

### Given
- Any seeded corpus.

### When
1. Search `query="design memo"`, `top_k=3`.

### Then
- Every hit exposes a non-empty `source_path`, a `heading`
  (may be the file name or a recovered H1/H2), and a
  `chunk_index` (integer ≥ 0).

---

## Scenario: HP-RETR-05 — hybrid search catches exact MPN via BM25
Validates: MET-335 (hybrid retrieval)
Tier: 1

### Given
- A datasheet or BOM chunk that contains the literal token
  "STM32H723VGT6".

### When
1. Search `query="STM32H723VGT6"`, `top_k=5`.

### Then
- The top hit's content contains the exact MPN string (BM25
  finds the literal token even when cosine similarity alone is
  weak).

---

## Scenario: HP-RETR-06 — reranker improves top result
Validates: MET-335 (reranker integration)
Tier: 1

### Given
- `KNOWLEDGE_RERANKER_ENABLED=true` in the gateway environment so
  the cross-encoder reranker (`BAAI/bge-reranker-base`) is wired
  into `LightRAGKnowledgeService.search`. If the env var is unset
  or `false`, this scenario reports BLOCKED with an environment
  note (not FAIL) — the reranker is opt-in by design.
- A corpus where one chunk is technically the most relevant but
  has lower raw cosine score than a noisier near-duplicate.

### When
1. Call `knowledge.search(query=..., top_k=3, rerank=False)` —
   capture the top hit. (This is the raw cosine baseline.)
2. Call `knowledge.search(query=..., top_k=3, rerank=True)` —
   capture the top hit. (This routes through the cross-encoder.)

### Then
- The reranked top hit is judged as relevant or more relevant
  than the raw cosine top hit. (LLM-graded acceptance — Claude
  assesses the two answers and reports which is on-topic. The
  judgement is intrinsically subjective; a tie counts as PASS.)

---

## Scenario: HP-RETR-07 — search via MCP `knowledge.search` tool
Validates: MET-346 (MCP tool path)
Tier: 1

### Given
- A unique seed chunk reachable via both REST and MCP paths.

### When
1. Call `knowledge.search` via the MCP transport with `top_k=3`.
2. Call the equivalent REST endpoint (`POST /search`) with the
   same query and `top_k`.

### Then
- The two responses contain the same set of `source_path` values
  in the same order. (Score ties may permute within ε; the set is
  identical.)

---

## Scenario: HP-RETR-08 — latency target under 200ms (1k-doc corpus)
Validates: MET-335, MET-401
Tier: 1

### Given
- A corpus of ≥ 1000 chunks (use seed data or HP-INGEST batch).

### When
1. Run 20 searches with varied queries, capturing wall-clock per
   call.

### Then
- p95 latency across the 20 calls is < 200ms on standard dev
  hardware. (If hardware is constrained, scenario reports
  BLOCKED with environment notes — not FAIL.)

---

## Scenario: HP-RETR-09 — empty query returns empty/clean response
Validates: MET-346 (edge case — pinned behavior)
Tier: 1

### Given
- (none)

### When
1. Call `knowledge.search` with `query=""`.

### Then
- The response is **either** `results=[]` cleanly, **or** a
  structured error with `code="invalid_input"` (per pinned
  behavior, MET-385 envelope).
- The MCP transport does not crash; the next call still works.

---

## Scenario: HP-RETR-10 — top_k=1 returns exactly 1 hit
Validates: MET-293 (cap)
Tier: 1

### Given
- A corpus with ≥ 2 matches for the query.

### When
1. Search with `top_k=1`.

### Then
- `len(results) == 1` exactly. The cap is honored even when
  more matches exist.

---

## Scenario: HP-RETR-11 — multi-filter compound query (AND across keys + type)
Validates: MET-293, MET-307, MET-417 (KB-SRC-012; pinned AND semantics, L1-B5)
Tier: 1

### Given
- Corpus with `{component, design_decision} × {project_A,
  project_B}` pre-seeded (four logical buckets — e.g. an MCU
  component chunk under project A, an MCU component chunk under
  project B, an MCU design_decision under project A, an MCU
  design_decision under project B).

### When
1. Call `knowledge.search` with `query="MCU"`, `top_k=5`,
   `knowledge_type="component"`, and
   `filters={"project_id": "A"}`.

### Then
- Every returned hit has `knowledge_type == "component"`.
- Every returned hit's metadata `project_id == "A"`.
- No hits leak from project B, and no hits with
  `knowledge_type == "design_decision"` leak through (filters
  AND across keys per the pinned contract in
  `docs/architecture/knowledge-ingestion-playbook.md#search-filters`).

---

## Scenario: HP-RETR-12 — missing project_id falls back to default tenant
Validates: MET-401 (KB-CTX-004; default-tenant scope, L1-A1 + L1-B3)
Tier: 1

### Given
- A clean session in which the per-call MCP context carries no
  `project_id` (i.e. `current_context().project_id is None`).

### When
1. Call `knowledge.ingest` with no `project_id` set in the
   per-call context (ingest a unique seed chunk).
2. Call `knowledge.search` with no `project_id` set in the
   per-call context, querying for the seed chunk.

### Then
- The ingest succeeds and the chunk is scoped to the `"default"`
  tenant (the documented fallback behavior).
- The search returns the seed chunk — i.e. it surfaces hits from
  the `"default"` tenant only.
- Ingest and search share the same fallback rule (a chunk
  ingested with no `project_id` is reachable by a search with no
  `project_id`, and vice versa).

---

## Acceptance

- All 12 scenarios PASS.
- Report committed.
