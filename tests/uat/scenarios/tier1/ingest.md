# Tier-1 — knowledge ingestion happy path (HP-INGEST)

Validates: MET-410 (sub-deliverable 1/10 of MET-409).
Tier: 1
Run: `/uat-cycle12 --tier 1 --scenario ingest`

Ten happy-path scenarios that exercise the knowledge ingestion
surface end-to-end from a Claude-as-real-user perspective. Every
scenario assumes the MetaForge MCP server is connected and the
`knowledge.ingest` tool plus the `forge ingest` CLI are reachable.

> **Note:** Some scenarios (HP-INGEST-04 row-level CSV chunking,
> HP-INGEST-10 event-driven ingest) depend on cycle-1/2 deliverables
> that may not be fully wired in every dev environment. If the
> capability is missing, the scenario reports BLOCKED, not FAIL.

---

## Scenario: HP-INGEST-01 — single markdown design memo via CLI
Validates: MET-336, MET-346
Tier: 1

### Given
- A markdown fixture at `tests/fixtures/knowledge/sample.md`.

### When
1. Run `forge ingest tests/fixtures/knowledge/sample.md`.
2. Read `metaforge://knowledge/sources` via MCP `resources/read`.

### Then
- Step 1 returns `IngestResult.chunks_indexed >= 1`.
- The source path appears in the resources list with non-empty
  `indexedAt` and `fragmentCount >= 1`.

---

## Scenario: HP-INGEST-02 — recursive directory ingest
Validates: MET-336
Tier: 1

### Given
- A directory `tests/fixtures/knowledge/` with mixed `.md` and
  `.pdf` files.

### When
1. Run `forge ingest tests/fixtures/knowledge/` (recursive).

### Then
- Every `.md` and `.pdf` file is dispatched (per-file outcome
  reported in the CLI output).
- Each file's `chunks_indexed` is reported individually.
- Final summary lists total files attempted and total chunks
  indexed.

---

## Scenario: HP-INGEST-03 — PDF datasheet ingest
Validates: MET-399 (PDF ingest)
Tier: 1
Status: executable (L1-A3 wired pdfplumber as the parser; raganything
remains the long-term home — see `digital_twin/knowledge/lightrag_service.py`
`_extract_pdf_text`).

### Given
- A 5-page committed PDF datasheet fixture at
  `tests/fixtures/knowledge/datasheet_excerpt.pdf` (STM32H743 excerpt).

### When
1. Ingest the PDF via `knowledge.ingest` (or CLI) with
   `knowledge_type="component"`. The gateway sniffs the latin-1
   `%PDF-` magic bytes, runs pdfplumber, prepends each page with a
   `## Page N` H2 header, and feeds the result into the existing
   heading-aware chunker.
2. Search for a phrase known to live on page ≥ 2 (e.g.
   `industrial grade temperature -40 to +85`, which sits on page 4 of
   the committed fixture).

### Then
- Step 1 returns `chunks_indexed > 1` (multi-page chunking).
- Step 2 returns at least one hit whose `heading` (or `metadata`)
  contains `Page N` for some integer `N`.

---

## Scenario: HP-INGEST-04 — CSV BOM rows → row-level chunks
Validates: MET-336 (walker), MET-346, MET-340 (CSV row chunker)
Tier: 1
Status: executable (L1-A4 wired `chunk_csv` in
`digital_twin/knowledge/chunker.py`; `LightRAGKnowledgeService.ingest`
detects `.csv` extension or `metadata.content_type=text/csv` and emits
one chunk per data row).

### Given
- A 5-row BOM CSV fixture at `tests/fixtures/knowledge/bom.csv` with
  columns `mpn,manufacturer,package,price`. The third data row
  (index 2) is `TPS62840DLCR` — used as the search target below.

### When
1. Ingest the CSV with `knowledge_type="component"` (via `forge ingest
   tests/fixtures/knowledge/bom.csv` or the `knowledge.ingest` MCP tool).
2. Search for `TPS62840DLCR` with `top_k=1`.

### Then
- Step 1 returns `IngestResult.chunks_indexed == 5` (one chunk per
  data row; the header is excluded).
- Step 2 returns at least one hit whose `content` contains
  `TPS62840DLCR` and whose metadata exposes `row_index` (the rendered
  content matches the `col=val; col=val` form, e.g.
  `mpn=TPS62840DLCR; manufacturer=Texas Instruments; package=SOT-563; price=1.20`).

---

## Scenario: HP-INGEST-05 — heading-aware chunking preserves H1/H2/H3
Validates: MET-335 (citation enrichment)
Tier: 1

### Given
- A markdown fixture with at least one H2 heading whose body has
  a unique-token phrase.

### When
1. Ingest the file.
2. Search for the unique-token phrase with `top_k=1`.

### Then
- The hit's citation `heading` field equals the H2 (or its
  parent H1 + H2 path), not just the file name.
- `chunk_index` is populated and consistent with the heading's
  position in the document.

---

## Scenario: HP-INGEST-06 — re-ingest dedup
Validates: MET-307, MET-346
Tier: 1

### Given
- A fixture file already ingested once in this session.

### When
1. Ingest the same file a second time at the same source_path.
2. Re-read `metaforge://knowledge/sources/{id}`.

### Then
- The second `chunks_indexed` equals the first run's count
  (deterministic, not doubled).
- The total chunk count under that source has not grown.

---

## Scenario: HP-INGEST-07 — explicit knowledge_type override
Validates: MET-307
Tier: 1

### Given
- A markdown fixture `decisions/2026-q1-mcu.md` with content
  describing an MCU pick.

### When
1. Ingest with explicit `knowledge_type="design_decision"`.
2. Search with a query matching the content **and** filter
   `knowledge_type="design_decision"`.

### Then
- The hit appears in the filtered result.
- A repeat search filtered by `knowledge_type="failure"` does
  **not** return the same hit.

---

## Scenario: HP-INGEST-08 — metadata pass-through
Validates: MET-401 (project scoping), MET-387 (per-call context)
Tier: 1

### Given
- A unique `work_product_id` UUID and a unique `project_id` UUID.

### When
1. Ingest a fixture with both UUIDs supplied as metadata.
2. Search for the content with `top_k=1`.

### Then
- The returned `SearchHit.metadata` (or top-level fields) carry
  both UUIDs back unchanged.
- Searching from a different `project_id` context does not
  return the hit (project isolation, MET-401).

---

## Scenario: HP-INGEST-09 — ingest via knowledge.ingest MCP tool
Validates: MET-346 (MCP tool path)
Tier: 1

### Given
- A unique source_path: `uat://tier1/ingest/mcp-direct`.

### When
1. Call `knowledge.ingest` directly (MCP, not CLI) with
   inline content.
2. Call `knowledge.search` for the content.

### Then
- The MCP-direct ingest returns the same `IngestResult` shape
  as the CLI path.
- The chunk is searchable in the same session — no manual
  refresh required.

---

## Scenario: HP-INGEST-10 — event-driven ingest via Kafka
Validates: MET-307 (KnowledgeConsumer wiring)
Tier: 1

### Given
- The Kafka broker and `KnowledgeConsumer` are running
  (otherwise: BLOCKED).

### When
1. Publish a `WORK_PRODUCT_CREATED` event whose payload references
   a small markdown asset.
2. Within 5 seconds, search for content from the asset.

### Then
- The search returns at least one hit referencing the
  work-product asset.
- The `KnowledgeSource` row is visible via
  `metaforge://knowledge/sources` with `status="indexed"`.

---

## Acceptance

- All 10 scenarios PASS in a single `/uat-cycle12 --tier 1
  --scenario ingest` invocation.
- Report committed under `docs/uat/uat-claude-driven-report-<date>.md`.
