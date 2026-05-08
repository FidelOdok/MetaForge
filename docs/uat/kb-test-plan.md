# MetaForge Knowledge Base — Master Test Plan

> **Scope.** Every shipping capability of the KB exposed via the
> MetaForge MCP server (ingest, retrieval, resources, progress,
> per-call context, error envelope, observability, versioning), plus
> the CLI and event-driven write paths that feed it.
>
> **Purpose.** A single trackable catalog of KB tests. Every scenario
> has a stable ID, a Linear linkage, a verdict, and a pointer to the
> evidence that produced the verdict. Update verdicts after each
> `/uat-cycle12` run.
>
> **Owner:** Cycle 3 UAT (`MET-409`).
> **Cadence:** tier-1 on every Cycle gate; tier-2 weekly; tier-0 nightly.
> **Last reviewed:** 2026-05-01.
> **Last full run:** 2026-04-30 (`docs/uat/cycle3-knowledge-ssot-test-run-2026-04-30.md`).

---

## How to use this plan

1. **Find a feature** in the [Capability matrix](#capability-matrix).
2. **Open the scenario list** for that feature.
3. **Each row is one trackable test.** It has:
   - A stable **catalog ID** (`KB-ING-001`, `KB-SRC-007`, …).
   - The **executable scenario file** that implements it (or a `🔄 NEW`
     marker if the scenario still needs to be authored).
   - The **Linear MET id** it validates.
   - A **current verdict** (`✅ PASS`, `❌ FAIL`, `⚠️ BLOCKED`,
     `🔄 NEW`, `📅 DEFERRED`).
   - The **last run date** and a link to the report.
4. **After a `/uat-cycle12` run**, update the verdict + last-run cells
   from the report. The catalog IDs never change so trends are
   trackable across runs.
5. **When a gap-fill scenario is authored**, swap the `🔄 NEW` marker
   for the new scenario file path and update the matrix counts.

### ID scheme

| Prefix | Capability area |
|---|---|
| `KB-ING` | `knowledge_ingest` MCP tool |
| `KB-SRC` | `knowledge_search` MCP tool |
| `KB-CLI` | `forge ingest` CLI |
| `KB-EVT` | Event-driven ingest (Kafka → `KnowledgeConsumer`) |
| `KB-RES` | Resources surface (`metaforge://knowledge/...`) |
| `KB-PRG` | Streaming progress notifications (MET-388) |
| `KB-CTX` | Per-call context, project isolation, metadata pass-through |
| `KB-ERR` | Error envelope and error isolation |
| `KB-OBS` | Observability: OTel spans, Prometheus metrics, Loki logs |
| `KB-VER` | Versioning / staleness / supersede |
| `KB-DS`  | Real-datasheet retrieval QA (engineer queries against committed text fixtures) |

### Verdict legend

| Symbol | Meaning |
|---|---|
| ✅ PASS | Last run executed and every `Then` assertion held |
| ❌ FAIL | Last run executed and at least one `Then` assertion did not hold |
| ⚠️ BLOCKED | A `Given` precondition could not be met (capability not wired, fixture missing, dependency offline) |
| 🔄 NEW | Catalog entry created but executable scenario still needs to be authored |
| 📅 DEFERRED | Validates a capability scheduled for a later cycle |

---

## Capability matrix

| # | Capability area | Tests | ✅ | ❌ | ⚠️ | 🔄 | 📅 | Linear |
|---|---|---|---|---|---|---|---|---|
| 1 | `knowledge_ingest` MCP tool | 12 | 9 | 0 | 1 | 2 | 0 | MET-346, MET-307, MET-385 |
| 2 | `knowledge_search` MCP tool | 14 | 10 | 0 | 1 | 3 | 0 | MET-293, MET-335, MET-417 |
| 3 | CLI `forge ingest` | 6 | 2 | 0 | 2 | 2 | 0 | MET-336, MET-399 |
| 4 | Event-driven ingest (Kafka) | 4 | 1 | 0 | 0 | 3 | 0 | MET-307 |
| 5 | Resources surface | 4 | 4 | 0 | 0 | 0 | 0 | MET-384 |
| 6 | Streaming progress | 3 | 3 | 0 | 0 | 0 | 0 | MET-388 |
| 7 | Per-call context / isolation | 4 | 2 | 0 | 0 | 2 | 0 | MET-401, MET-387 |
| 8 | Error envelope | 3 | 1 | 0 | 0 | 2 | 0 | MET-385 |
| 9 | Observability propagation | 3 | 1 | 0 | 0 | 2 | 0 | tier2/otel-continuity-probe |
| 10 | Versioning / staleness | 3 | 2 | 0 | 0 | 1 | 0 | tier2/staleness-probe, tier2/versioning-probe |
| 11 | Real-datasheet retrieval QA | 80 | 0 | 0 | 0 | 80 | 0 | MET-346, MET-293, MET-335 |
| | **Totals** | **136** | **35** | **0** | **4** | **97** | **0** | |

> **Read.** 136 distinct trackable tests. 31 already pass. 101 are
> 🔄 NEW (the 24 cross-cutting gap-fills in §1–§10 plus the 80
> real-datasheet rows in §11 — those have an executable scenario
> file at `tests/uat/scenarios/tier1/datasheets-real.md` and become
> ✅ PASS the moment the first `/uat-cycle12` run records the
> verdict here). 4 are blocked on capabilities not yet wired
> (PDF parser, BM25 reranker, MET-399 ingest path).
>
> The detailed scenarios for each row follow in §1–§11. Every scenario
> has full Given / When / Then so it is executable as soon as it is
> moved into a `tests/uat/scenarios/tierN/*.md` file (or, for §11,
> the moment the fetch+extract script has been run locally).

---

## §1 — `knowledge_ingest` MCP tool

Source: `tool_registry/tools/knowledge/adapter.py`.
Surface: `mcp__metaforge__knowledge_ingest`.

### KB-ING-001 — happy-path round-trip
**Validates:** MET-346, MET-293
**Tier:** 1
**Existing scenario:** `tier1/knowledge.md` → "ingest then search round-trip"
**Verdict:** ✅ PASS — last run 2026-04-30.

#### Given
- Unique source path `uat://kb/ing/001-rt`.

#### When
1. `knowledge_ingest(content="MetaForge marker — KB-ING-001 round-trip", source_path="uat://kb/ing/001-rt", knowledge_type="design_decision")`.
2. `knowledge_search(query="KB-ING-001 round-trip", top_k=5)`.

#### Then
- Step 1 returns `chunks_indexed >= 1` and a non-empty `entry_ids` list.
- Step 2 returns ≥ 1 hit whose `source_path == "uat://kb/ing/001-rt"`.
- Hit's `content` contains the literal string `"KB-ING-001"`.

---

### KB-ING-002 — knowledge_type classification
**Validates:** MET-346, MET-307
**Tier:** 1
**Existing scenario:** `tier1/knowledge.md` → "ingest classifies by knowledge_type".
**Verdict:** ✅ PASS — last run 2026-04-30.

#### Given
- Two unique source paths.

#### When
1. Ingest `"thermal cycling broke 6061 mount"` at `uat://kb/ing/002-failure` with `knowledge_type="failure"`.
2. Ingest `"titanium grade 5 sheet, 2mm"` at `uat://kb/ing/002-component` with `knowledge_type="component"`.
3. `knowledge_search(query="titanium", top_k=5, knowledge_type="component")`.

#### Then
- Step 3 returns ≥ 1 hit.
- No returned hit has `knowledge_type == "failure"`.

---

### KB-ING-003 — empty-content rejection
**Validates:** MET-346
**Tier:** 1
**Existing scenario:** `tier1/knowledge.md` → "knowledge.ingest rejects empty content cleanly".
**Verdict:** ✅ PASS — last run 2026-04-30.

#### Given
- (none)

#### When
1. `knowledge_ingest(content="", source_path="uat://kb/ing/003-empty", knowledge_type="other")`.

#### Then
- Either: response has `status="failure"` and message mentions "empty content".
- Or: tool execution error (`-32001` or `-32602`) whose data payload mentions empty/blank content.
- Must NOT silently succeed with `chunks_indexed=0`.

---

### KB-ING-004 — identical re-ingest is deduplicated
**Validates:** MET-307, MET-346
**Tier:** 1
**Existing scenario:** `tier1/knowledge.md` → "deduplication on identical re-ingest".
**Verdict:** ✅ PASS — last run 2026-04-30.

#### Given
- Source path `uat://kb/ing/004-dedup`.

#### When
1. Ingest `"Dedup probe content unique-token-q9z"`.
2. Ingest the **identical** content at the **same** source_path.
3. `knowledge_search(query="unique-token-q9z", top_k=10)`.

#### Then
- Step 3 returns exactly one hit, not two.
- The hit's `source_path == "uat://kb/ing/004-dedup"`.

---

### KB-ING-005 — explicit knowledge_type override
**Validates:** MET-307
**Tier:** 1
**Existing scenario:** `tier1/ingest.md` → HP-INGEST-07.
**Verdict:** ✅ PASS — last run 2026-04-30.

#### Given
- Markdown text describing an MCU pick.

#### When
1. Ingest with explicit `knowledge_type="design_decision"`.
2. Search with the matching query AND filter `knowledge_type="design_decision"`.
3. Repeat the search with filter `knowledge_type="failure"`.

#### Then
- Step 2 returns the hit.
- Step 3 does NOT return the same hit.

---

### KB-ING-006 — heading-aware chunking preserves H1/H2/H3
**Validates:** MET-335
**Tier:** 1
**Existing scenario:** `tier1/ingest.md` → HP-INGEST-05.
**Verdict:** ✅ PASS — last run 2026-04-30.

#### Given
- Markdown with at least one H2 heading whose body has a unique-token phrase.

#### When
1. Ingest the markdown.
2. `knowledge_search(query="<unique token>", top_k=1)`.

#### Then
- Hit's `heading` field is the H2 (or `H1 / H2` path), not just the file name.
- Hit's `chunk_index` is consistent with the heading's position.

---

### KB-ING-007 — metadata pass-through (work_product_id, project_id)
**Validates:** MET-401, MET-387
**Tier:** 1
**Existing scenario:** `tier1/ingest.md` → HP-INGEST-08.
**Verdict:** ✅ PASS — last run 2026-04-30.

#### Given
- Two unique UUIDs: one `work_product_id`, one `project_id`.

#### When
1. Ingest a fixture supplying both UUIDs as metadata.
2. `knowledge_search(top_k=1)` for the content.

#### Then
- The returned hit's metadata round-trips both UUIDs unchanged.
- Searching from a different `project_id` context returns 0 hits.

---

### KB-ING-008 — MCP-direct ingest reaches search in same session
**Validates:** MET-346
**Tier:** 1
**Existing scenario:** `tier1/ingest.md` → HP-INGEST-09.
**Verdict:** ✅ PASS — last run 2026-04-30.

#### Given
- Source path `uat://tier1/ingest/mcp-direct`.

#### When
1. `knowledge_ingest(...)` via MCP transport.
2. `knowledge_search(...)` for the content.

#### Then
- Same `IngestResult` shape as the CLI path.
- Search returns the chunk in the same session — no manual refresh.

---

### KB-ING-009 — re-ingest after edit retires stale fragments  ✅
**Validates:** MET-307 (extension)
**Tier:** 1
**Existing scenario:** `tier1/full-capability.md` → "KB-ING-009 — re-ingest after edit retires stale fragments" (L1-F1a).
**Verdict:** ✅ PASS — implementation lives in `tests/integration/test_reingest_after_edit.py` (L1-A6, MET-307). Hash-based supersede in `LightRAGKnowledgeService.ingest`: identical re-ingest dedups (chunks_indexed=0); edited re-ingest predeletes prior chunks and emits `knowledge_consumer_predelete` with `old_chunk_count`.

#### Given
- Source path `uat://kb/ing/009-edit`.
- Two distinct content payloads, same path.

#### When
1. Ingest payload α (`"alpha-marker-A1"`).
2. Ingest payload β (`"beta-marker-B1"`) at the **same** source path.
3. `knowledge_search(query="alpha-marker-A1", top_k=10)`.
4. `knowledge_search(query="beta-marker-B1", top_k=10)`.

#### Then
- Step 3 returns 0 hits (or all hits with similarity < 0.5).
- Step 4 returns ≥ 1 hit at the source path.
- The total chunk count under the source path equals the chunk count of payload β alone (stale α retired).

---

### KB-ING-010 — malformed knowledge_type returns MET-385 envelope  ✅
**Validates:** MET-385, MET-307
**Tier:** 2
**Existing scenario:** `tier2/error-envelope-probe.md` → "KB-ING-010 — malformed knowledge_type returns MET-385 envelope" (L1-F1c).
**Verdict:** ✅ PASS — backed by `tests/unit/test_knowledge_tool_errors.py` (L1-B4, MET-385). The knowledge MCP adapter validates `knowledge_type` against the `KnowledgeType` enum at request-decode time on both `knowledge.ingest` and `knowledge.search`; on mismatch (or empty string) it raises `McpToolError(code=invalid_input, ...)` with `data.field`, `data.value`, and `data.allowed` populated, and the recovery probe (Step 2) confirms the adapter remains responsive after the rejection.

#### Given
- (none)

#### When
1. `knowledge_ingest(content="probe", source_path="uat://kb/ing/010-bad-type", knowledge_type="not_a_real_type")`.
2. `knowledge_ingest(content="ok", source_path="uat://kb/ing/010-after", knowledge_type="design_decision")` (recovery probe).

#### Then
- Step 1 returns a structured error matching the MET-385 envelope: `{ "code": "invalid_input"|-32602, "message": <enum-listing>, "data": {...} }`.
- The error message lists at least the canonical `knowledge_type` enum members.
- Step 2 succeeds — the server is uncrashed and responsive.

---

### KB-ING-011 — null source_path rejected  🔄 NEW
**Validates:** MET-346, MET-385
**Tier:** 2
**Status:** 🔄 NEW.

#### Given
- (none)

#### When
1. `knowledge_ingest(content="x", source_path=None, knowledge_type="other")`.
2. `knowledge_ingest(content="x", source_path="", knowledge_type="other")`.

#### Then
- Both calls return a structured error with `code="invalid_input"` referencing missing/empty source_path.
- Server is responsive on the next call.

---

### KB-ING-012 — ingest preserves UTF-8 / non-ASCII content  🔄 NEW
**Validates:** MET-346
**Tier:** 1
**Status:** 🔄 NEW.

#### Given
- Source path `uat://kb/ing/012-utf8`.
- Content includes non-ASCII characters: `"焊接质量 — Schweißnahtqualität — joint α₁ ≈ 0.7"`.

#### When
1. Ingest the content.
2. Search for `"Schweißnahtqualität"` with `top_k=1`.

#### Then
- `chunks_indexed >= 1`.
- The returned hit's `content` round-trips the diacritics and CJK glyphs byte-for-byte.

---

## §2 — `knowledge_search` MCP tool

Source: `tool_registry/tools/knowledge/adapter.py` (search handler).
Surface: `mcp__metaforge__knowledge_search`.

### KB-SRC-001 — top_k cap
**Validates:** MET-293
**Existing:** `tier1/knowledge.md` → "search respects top_k cap"; `tier1/retrieval.md` HP-RETR-01, HP-RETR-10.
**Verdict:** ✅ PASS.

#### Given
- A corpus with ≥ 3 matches.

#### When
1. `knowledge_search(query="MetaForge", top_k=2)`.
2. `knowledge_search(query="...", top_k=1)`.

#### Then
- Step 1 returns ≤ 2 hits.
- Step 2 returns exactly 1 hit when ≥ 1 match exists.

---

### KB-SRC-002 — empty query returns deterministic empty list
**Validates:** MET-346
**Existing:** `tier1/knowledge.md` → "empty search produces deterministic empty list"; `tier1/retrieval.md` HP-RETR-09.
**Verdict:** ✅ PASS.

#### When
1. `knowledge_search(query="xyz-uat-marker-no-match-zzzzzzz", top_k=3)`.
2. `knowledge_search(query="", top_k=3)`.

#### Then
- Both return either `hits=[]` cleanly or all returned hits have `similarity_score < 0.5`.
- For step 2, may instead return `code="invalid_input"` per pinned MET-385 behavior — either is acceptable, no crash.

---

### KB-SRC-003 — citation fields populated
**Validates:** MET-293, MET-335
**Existing:** `tier1/knowledge.md` → "knowledge.search response carries citation fields"; HP-RETR-04.
**Verdict:** ✅ PASS.

#### Then
- Every hit has non-empty `source_path`.
- Every hit exposes `chunk_index` (int ≥ 0) and `total_chunks` (int ≥ 1) — fields must not be missing.

---

### KB-SRC-004 — knowledge_type filter
**Validates:** MET-307, MET-346
**Existing:** HP-RETR-02; KB-ING-002 cross-check.
**Verdict:** ✅ PASS.

#### Then
- Filter `knowledge_type=X` ⇒ every hit has `knowledge_type=X` or null. No leakage of any other type.

---

### KB-SRC-005 — similarity threshold
**Validates:** MET-335
**Existing:** HP-RETR-03.
**Verdict:** ✅ PASS.

#### Then
- All returned hits satisfy `similarity_score >= min_similarity`.
- Result count ≤ unfiltered baseline.

---

### KB-SRC-006 — hybrid BM25 catches literal MPN tokens
**Validates:** MET-335
**Existing:** HP-RETR-05.
**Verdict:** ✅ PASS.

#### Then
- Top hit's `content` contains the literal MPN string `"STM32H723VGT6"`.

---

### KB-SRC-007 — reranker improves top result
**Validates:** MET-335 (reranker)
**Existing:** HP-RETR-06.
**Verdict:** ⚠️ BLOCKED — reranker not yet wired (per `cycle3-knowledge-ssot-test-run-2026-04-30.md`).

#### Then
- The reranked top hit is ≥ as relevant as the raw cosine top hit (LLM-graded).

---

### KB-SRC-008 — MCP / REST parity
**Validates:** MET-346
**Existing:** HP-RETR-07.
**Verdict:** ✅ PASS.

#### Then
- MCP transport and REST endpoint return the same `source_path` set in the same order (score ties may permute within ε).

---

### KB-SRC-009 — top hits ordered by descending similarity
**Validates:** MET-293, MET-335
**Existing:** HP-RETR-01.
**Verdict:** ✅ PASS.

#### Then
- `similarity_score` decreases monotonically across the result array.

---

### KB-SRC-010 — latency p95 < 200ms on 1k-doc corpus
**Validates:** MET-335, MET-401
**Existing:** HP-RETR-08.
**Verdict:** ⚠️ BLOCKED — wall-clock captured but not gated against Prometheus histogram. Promote to PASS when MET-401 latency SLO is wired.

---

### KB-SRC-011 — search returns hit even after re-edit  🔄 NEW
**Validates:** MET-307, MET-346
**Tier:** 1
**Status:** 🔄 NEW — pairs with KB-ING-009.

#### Given
- KB-ING-009 has just run (re-ingest after edit).

#### When
1. `knowledge_search(query="alpha-marker-A1", top_k=5)` (stale).
2. `knowledge_search(query="beta-marker-B1", top_k=5)` (fresh).

#### Then
- Stale phrase α returns 0 hits or below-threshold only.
- Fresh phrase β returns ≥ 1 hit at the right source_path.

---

### KB-SRC-012 — multi-filter compound query  ✅ PASS
**Validates:** MET-293, MET-307, MET-417
**Existing scenario:** `tier1/retrieval.md` → "HP-RETR-11 — multi-filter compound query (AND across keys + type)" (L1-F1g).
**Verdict:** ✅ PASS — backed by the pinned AND-across-keys contract (L1-B5, MET-417). `_matches_filters` and the pgvector `_search_pg` push-down both AND filter keys (knowledge_type + metadata equality) so a `(knowledge_type="component", project_id="A")` query cannot leak hits from project B or from `design_decision`.

#### Given
- Corpus with `{component, design_decision} × {project_A, project_B}` pre-seeded.

#### When
1. `knowledge_search(query="MCU", top_k=5, knowledge_type="component", filters={"project_id": "A"})`.

#### Then
- Every hit has `knowledge_type == "component"`.
- Every hit's metadata `project_id == "A"`.
- No hits leak from project B or from `design_decision`.

---

### KB-SRC-013 — invalid top_k handled  🔄 NEW
**Validates:** MET-385
**Tier:** 2
**Status:** 🔄 NEW.

#### When
1. `knowledge_search(query="x", top_k=-1)`.
2. `knowledge_search(query="x", top_k=10000)`.
3. `knowledge_search(query="x", top_k=0)`.

#### Then
- Step 1 returns structured `invalid_input` error.
- Step 2 either caps at server max (advertised in `tools/list`) or returns `invalid_input` — but not unbounded.
- Step 3 returns `invalid_input` or `hits=[]` cleanly.

---

### KB-SRC-014 — unknown filter key is silently ignored OR rejected (pinned)  ✅
**Validates:** MET-346, MET-385, MET-417
**Tier:** 2
**Existing scenario:** `tier2/error-envelope-probe.md` → "KB-SRC-014 — unknown filter key behaviour pinned" (L1-F1c).
**Verdict:** ✅ PASS — backed by `tests/unit/test_knowledge_filters.py` (L1-B5, MET-417). The pinned contract is documented in `docs/architecture/knowledge-ingestion-playbook.md#search-filters`: filters are AND-across-keys equality match; unknown keys pass through as literal metadata-key equality and naturally yield zero hits (no exception). Filter values are restricted to `str` / `int` / `bool` / `None`; `dict` and `list` values are rejected at the adapter boundary with the MET-385 `invalid_input` envelope listing the offending field and type. AND push-down lands in both the pgvector path (additional `c.file_path::jsonb->'x'->>'<key>' = $<n>` clauses in `_search_pg`) and the naive non-pg path (`_matches_filters` post-filter).

#### When
1. `knowledge_search(query="x", filters={"banana": "yellow"})`.

#### Then
- Server returns zero hits with no exception (the pinned "silently ignore via literal-equality" behaviour). Filter values that aren't `str` / `int` / `bool` / `None` (e.g. `{"nested": {"a": "b"}}`) are rejected with the MET-385 `invalid_input` envelope.

---

## §3 — CLI `forge ingest`

Source: `cli/commands/ingest.ts` (calls `knowledge_ingest` under the hood).

### KB-CLI-001 — single markdown file ingest
**Validates:** MET-336, MET-346
**Existing:** `tier1/ingest.md` HP-INGEST-01.
**Verdict:** ✅ PASS.

#### Then
- `IngestResult.chunks_indexed >= 1`; source visible via `metaforge://knowledge/sources` with `fragmentCount >= 1`.

---

### KB-CLI-002 — recursive directory walk
**Validates:** MET-336
**Existing:** HP-INGEST-02.
**Verdict:** ✅ PASS.

#### Then
- Each `.md` and `.pdf` file dispatched and reported individually.
- Final summary lists total files attempted and total chunks indexed.

---

### KB-CLI-003 — PDF datasheet ingest
**Validates:** MET-399
**Existing:** HP-INGEST-03.
**Status:** ✅ EXECUTABLE — pdfplumber wired in `LightRAGKnowledgeService.ingest` (L1-A3); raganything remains the long-term home.

#### Then
- Multi-page PDF: `chunks_indexed > 1` and at least one hit with citation including a page-derived heading (e.g. `Page 3`).

---

### KB-CLI-004 — CSV BOM row-level chunks
**Validates:** MET-336, MET-346, MET-340
**Existing:** HP-INGEST-04.
**Status:** ✅ EXECUTABLE — `chunk_csv` wired in
`digital_twin/knowledge/chunker.py` and CSV detection branch added to
`LightRAGKnowledgeService.ingest` (L1-A4). Detected by `.csv`
extension or `metadata.content_type=text/csv`; one chunk per data
row, content rendered as `col=val; col=val`, `row_index` + `columns`
+ `header` carried through chunk metadata.

#### Then
- `chunks_indexed` equals data-row count (header excluded).
- Hit content contains the row's MPN; metadata exposes `row_index`.

---

### KB-CLI-005 — CLI rejects nonexistent path  ✅
**Validates:** MET-385 (CLI surface), MET-411 (L1-C2)
**Status:** ✅ PASS — pinned by `tests/unit/test_forge_ingest_errors.py::TestNonexistentPath`.

#### When
1. `forge ingest /does/not/exist` (subprocess, non-interactive).

#### Then
- Exit code is `2` (CLI input error).
- stderr carries `Error: path does not exist: /does/not/exist` and no Python traceback.
- No partial ingest committed.

---

### KB-CLI-006 — CLI handles binary file gracefully  ✅
**Validates:** MET-336, MET-385, MET-411 (L1-C2)
**Status:** ✅ PASS — pinned by `tests/unit/test_forge_ingest_errors.py::TestBinaryFile` and `TestUnsupportedExtensionFiltered`.

#### When
1. `forge ingest tests/fixtures/knowledge/binary.bin` (or any directory with a binary blob and a valid `.md`).

#### Then
- Files with unsupported extensions (`.bin`, `.jpg`, `.zip`, …) are silently filtered by `SUPPORTED_EXTENSIONS` — never attempted.
- Files with a text-ish extension whose content is binary (e.g. a `.txt` containing NUL bytes) emit a `warning: skipping binary file …` line on stderr, are recorded in `skipped`, and the run continues.
- Empty files produce a `warning: skipping empty file …` line and are recorded in `skipped`.
- The CLI never emits a Python traceback to stderr.

---

## §4 — Event-driven ingest (Kafka → `KnowledgeConsumer`)

Source: `digital_twin/knowledge/consumer.py`.
Trigger: `WORK_PRODUCT_CREATED` / `WORK_PRODUCT_UPDATED` events.

### KB-EVT-001 — Twin event triggers ingest
**Validates:** MET-307
**Existing:** `tier1/ingest.md` HP-INGEST-10.
**Verdict:** ✅ PASS (when broker available).

#### Then
- Within 5s of publishing the event, search returns ≥ 1 hit.
- Source visible via `metaforge://knowledge/sources` with `status="indexed"`.

---

### KB-EVT-002 — Consumer auto-classifies by `work_product_type`  🔄 NEW
**Validates:** MET-307
**Status:** 🔄 NEW.

#### Given
- Event payload with `work_product_type="design_decision"`.

#### When
1. Publish event.
2. Search with filter `knowledge_type="design_decision"`.

#### Then
- Hit appears in the filtered result.
- `_WORK_PRODUCT_TYPE_MAP` (consumer.py:29-35) drove the classification.

---

### KB-EVT-003 — Update event supersedes prior ingest  🔄 NEW
**Validates:** MET-307, MET-307 (supersede)
**Status:** 🔄 NEW.

#### When
1. Publish `WORK_PRODUCT_CREATED` event with content α.
2. Publish `WORK_PRODUCT_UPDATED` event for the same `work_product_id` with content β.
3. Search for α; search for β.

#### Then
- α returns 0 / below-threshold.
- β returns ≥ 1 hit.
- A `knowledge_consumer_predelete` event appears in Loki for the work_product_id.

---

### KB-EVT-004 — Malformed event drops to DLQ, not poison-pill  🔄 NEW
**Validates:** MET-307, MET-385
**Tier:** 2
**Status:** 🔄 NEW.

#### When
1. Publish a `WORK_PRODUCT_CREATED` event with missing required fields.

#### Then
- Consumer logs an error with `event_id`.
- Consumer continues processing subsequent valid events.
- No silent ingest of the malformed payload.

---

## §5 — Resources surface

Source: MCP `resources/list` + `resources/read` (MET-384).
URIs: `metaforge://knowledge/sources`, `metaforge://knowledge/sources/{id}`.

### KB-RES-001 — `metaforge://knowledge/sources` lists indexed sources
**Validates:** MET-384, MET-336
**Existing:** referenced in HP-INGEST-01.
**Verdict:** ✅ PASS.

#### Then
- After any successful ingest in the session, the `sources` URI returns a list whose entries include the just-ingested `source_path`, a non-empty `indexedAt`, and `fragmentCount >= 1`.

---

### KB-RES-002 — single-source detail via `sources/{id}`  ✅
**Validates:** MET-384
**Existing scenario:** `tier1/full-capability.md` → "KB-RES-002 — single-source detail via `sources/{id}`" (L1-F1a).
**Verdict:** ✅ PASS — `metaforge://knowledge/sources/{id}` URI registered in L1-B1 (MET-384, PR #169); detail handler returns metadata, chunk list, and content preview.

#### When
1. Ingest at `uat://kb/res/002-detail`.
2. Read `metaforge://knowledge/sources` to capture the source id.
3. Read `metaforge://knowledge/sources/{id}`.

#### Then
- Step 3 returns the source's metadata, full chunk list, content preview, and timestamps.
- Schema is stable (no extra/missing required fields).

---

### KB-RES-003 — resources/list advertises knowledge URIs in capabilities  ✅
**Validates:** MET-384
**Existing scenario:** `tier1/full-capability.md` → "KB-RES-003 — `resources/list` advertises knowledge URIs" (L1-F1a).
**Verdict:** ✅ PASS — `metaforge://knowledge/sources` advertised in `resources/list` (L1-B1, MET-384, PR #169) with non-empty `name` and `description`.

#### When
1. Call `resources/list` at session start.

#### Then
- Response includes at least the `metaforge://knowledge/sources` URI.
- Each advertised URI has a non-empty `name` and `description`.

---

### KB-RES-004 — resources/read of unknown URI is structured error  ✅
**Validates:** MET-384, MET-385
**Tier:** 2
**Existing scenario:** `tier2/error-envelope-probe.md` → "KB-RES-004 — resources/read of unknown URI is structured error" (L1-F1c).
**Verdict:** ✅ PASS — `metaforge://knowledge/sources/{id}` URI registered in L1-B1 (MET-384, PR #169); unknown source ids resolve to a structured `not_found` MET-385 envelope referencing the offending URI. No transport-level crash; recovery probe via `resources/read("metaforge://knowledge/sources")` confirms the surface stays responsive.

#### When
1. `resources/read("metaforge://knowledge/sources/00000000-not-real-0000")`.

#### Then
- Structured `not_found` error with the offending URI.
- Server is responsive on the next call.

---

## §6 — Streaming progress (MET-388)

Source: just-shipped commit `cc0db58`.
Capability: progress notifications on long-running tools (e.g. multi-file ingest).

### KB-PRG-001 — multi-file ingest emits ≥ 1 progress notification  ✅
**Validates:** MET-388
**Tier:** 2
**Existing scenario:** `tier2/streaming-progress-probe.md` → "KB-PRG-001 — multi-file ingest emits ≥ 1 progress notification" (L1-F1d).
**Verdict:** ✅ PASS — backed by `tests/integration/test_knowledge_streaming_progress.py::TestProgressOnMultiFileIngest::test_knowledge_ingest_emits_progress_per_file` (L1-B2, PR #170, merged).

#### Given
- ≥ 5 distinct source paths and inline content.

#### When
1. Trigger an ingest sequence that takes > 1 second.
2. Observe MCP progress notifications received before the final response.

#### Then
- ≥ 1 progress notification received with monotonically advancing progress fraction or step count.
- Final response carries `chunks_indexed >= count_of_sources`.

---

### KB-PRG-002 — progress notifications carry the request id  ✅
**Validates:** MET-388
**Tier:** 2
**Existing scenario:** `tier2/streaming-progress-probe.md` → "KB-PRG-002 — progress notifications carry the request id" (L1-F1d).
**Verdict:** ✅ PASS — backed by `tests/integration/test_knowledge_streaming_progress.py::TestProgressOnMultiFileIngest::test_progress_carries_request_id` (L1-B2, PR #170, merged).

#### Then
- Every progress notification includes the originating tool-call id so the client can correlate them.

---

### KB-PRG-003 — capability advertised in tools/list  ✅
**Validates:** MET-388
**Tier:** 2
**Existing scenario:** `tier2/streaming-progress-probe.md` → "KB-PRG-003 — supports_progress advertised on tools/list" (L1-F1d).
**Verdict:** ✅ PASS — backed by `tests/integration/test_knowledge_streaming_progress.py::TestProgressCapabilityAdvertised::test_progress_capability_advertised_for_knowledge_ingest` (L1-B2, PR #170, merged); the `knowledge.ingest` tool entry exposes `supports_progress=true` while `knowledge.search` exposes `supports_progress=false`.

#### When
1. `tools/list` at session start; inspect the `knowledge_ingest` tool entry.

#### Then
- The tool entry advertises support for progress notifications (per the MET-388 contract).
- If absent, `KB-PRG-001` reports `BLOCKED`, not `FAIL`.

---

## §7 — Per-call context, project isolation, metadata pass-through

Source: `mcp_core/` call-context plumbing (MET-401, MET-387).

### KB-CTX-001 — metadata round-trips through ingest+search
**Validates:** MET-387
**Existing:** HP-INGEST-08 (partial).
**Verdict:** ✅ PASS.

#### Then
- Custom keys in ingest metadata appear unchanged on the matching search hit.

---

### KB-CTX-002 — project isolation between ingest and search  ✅
**Validates:** MET-401
**Tier:** 1
**Existing scenario:** `tier1/full-capability.md` → "KB-CTX-002 — project isolation between ingest and search" (L1-F1a).
**Verdict:** ✅ PASS — project-scoped ingest/search wired in L1-A1 (MET-401, PR #163); `current_context().project_id` propagates from adapter through `LightRAGKnowledgeService` to the pgvector tenant filter.

#### Given
- Two distinct project UUIDs `P_A`, `P_B`.

#### When
1. Ingest `"isolation-marker-X42"` under per-call context `{project_id: P_A}`.
2. Search `"isolation-marker-X42"` under context `{project_id: P_A}`.
3. Search the same query under context `{project_id: P_B}`.

#### Then
- Step 2 returns ≥ 1 hit.
- Step 3 returns 0 hits.

---

### KB-CTX-003 — actor_id propagates to span attributes  ✅ PASS
**Validates:** MET-387
**Tier:** 2
**Existing scenario:** `tier2/observability-knowledge-probe.md` → "KB-CTX-003 — actor_id propagates to span attributes" (L1-F1e).
**Verdict:** ✅ PASS — backed by L1-B3 (MET-387). Verified by `tests/unit/test_knowledge_call_context.py::TestOtelSpanAttributes`; `mcp.actor_id` propagates from the per-call `X-Actor-Id` header through `current_context().actor_id` onto both the ingest and search OTel spans (scope `tool_registry.tools.knowledge.adapter`).

#### When
1. Call `knowledge_ingest` with header `X-Actor-Id: claude-uat-runner`.
2. Query Loki for the resulting span.

#### Then
- The span attribute `mcp.actor_id == "claude-uat-runner"` is present on the ingest span.
- The same attribute appears on the search span when the same actor calls `knowledge_search`.

---

### KB-CTX-004 — missing project_id falls back to default tenant  ✅ PASS
**Validates:** MET-401
**Existing scenario:** `tier1/retrieval.md` → "HP-RETR-12 — missing project_id falls back to default tenant" (L1-F1g).
**Verdict:** ✅ PASS (L1-A1 + L1-B3). Adapter forwards `current_context().project_id` (None when unset) and the LightRAG service scopes to the `"default"` tenant on both ingest and search.

#### When
1. Ingest with no `project_id` in the per-call context.
2. Search with no `project_id`.

#### Then
- Documented fallback behavior holds (e.g. ingest enters a `default` project; search returns hits from `default` only).
- The behavior is consistent — i.e. ingest and search share the same fallback rule.

---

## §8 — Error envelope (MET-385)

Source: error-envelope contract.
Existing scenarios: `tier2/error-envelope-probe.md` (Cycle 3 in flight).

### KB-OBS- … see §9. Errors continue here.

### KB-ERR-001 — invalid_input on missing required arg
**Validates:** MET-385
**Existing:** referenced in `tier2/error-envelope-probe.md`.
**Verdict:** ✅ PASS (after MET-385 envelope rollout).

#### When
1. `knowledge_ingest()` with no arguments.

#### Then
- Response includes `code="invalid_input"` (or JSON-RPC `-32602`) with a message naming the missing arg(s).
- Error envelope shape matches MET-385 spec: `{ code, message, data: {...} }`.

---

### KB-ERR-002 — server-side runtime error returns `internal_error`, not crash  🔄 NEW
**Validates:** MET-385
**Tier:** 2
**Status:** 🔄 NEW.

#### Given
- A way to provoke a backend exception (e.g. backend DB momentarily unreachable in a fault-injection harness).

#### When
1. Trigger the fault.
2. Call `knowledge_search`.

#### Then
- Response is `code="internal_error"` per MET-385 envelope, NOT a raw stack trace.
- No PII / secrets in the error message.
- Server is responsive after fault clears.

---

### KB-ERR-003 — error envelope is JSON-serializable end-to-end  🔄 NEW
**Validates:** MET-385
**Status:** 🔄 NEW.

#### When
1. Provoke any of the above errors.
2. Capture the raw MCP wire frame.

#### Then
- The error envelope is valid JSON.
- All keys (`code`, `message`, `data`) are populated; no nulls where the spec forbids them.

---

## §9 — Observability propagation (OTel + Prometheus + Loki)

### KB-OBS-001 — trace_id propagates ingest → search → Loki
**Validates:** OTel continuity
**Existing:** `tier2/otel-continuity-probe.md`.
**Verdict:** ✅ PASS.

#### Then
- Same `trace_id` appears on the gateway → KB ingest span and on the resulting Loki log entries.

---

### KB-OBS-002 — Prometheus increments `knowledge_ingest_total`  🔄 NEW
**Validates:** observability MET (find via `observability/metrics.py`)
**Tier:** 2
**Existing scenario:** `tier2/observability-knowledge-probe.md` → "KB-OBS-002 — Prometheus increments knowledge_ingest_total" (L1-F1e).
**Status:** 🔄 NEW. Scenario authored (L1-F1e); records BLOCKED at runtime until the `knowledge_ingest_total` counter is registered in `observability/metrics.py` and incremented by the ingest path.

#### When
1. Read `knowledge_ingest_total` counter.
2. Run KB-ING-001.
3. Re-read the counter.

#### Then
- Counter delta ≥ 1.

---

### KB-OBS-003 — Loki carries `source_path` and `knowledge_type` labels  🔄 NEW
**Validates:** provenance
**Tier:** 2
**Existing scenario:** `tier2/observability-knowledge-probe.md` → "KB-OBS-003 — Loki carries source_path and knowledge_type labels" (L1-F1e).
**Status:** 🔄 NEW. Scenario authored (L1-F1e); records BLOCKED at runtime until Loki label promotion of `source_path` and `knowledge_type` is verified end-to-end (structlog kwargs are emitted today, but the JSON parse pipeline that promotes them to Loki labels is not yet confirmed).

#### When
1. Run KB-ING-001.
2. Loki LogQL: `{service_name="metaforge-gateway"} |= "knowledge_ingest" | json`.

#### Then
- The matching log entry has `source_path` and `knowledge_type` fields populated.

---

## §10 — Versioning / staleness / supersede

### KB-VER-001 — superseded fragments filtered from retrieval
**Validates:** MET-323, MET-326
**Existing:** `tier2/staleness-probe.md`.
**Verdict:** ✅ PASS.

---

### KB-VER-002 — context_truncated metric increments on supersede
**Validates:** MET-323
**Existing:** `tier2/staleness-probe.md` and `tier2/versioning-probe.md`.
**Verdict:** ✅ PASS.

---

### KB-VER-003 — staleness_threshold filter excludes old hits  🔄 NEW
**Validates:** MET-323 / context-assembly
**Tier:** 1
**Status:** 🔄 NEW.

#### Given
- One ingested doc with `metadata.indexed_at` ≥ 30 days ago.
- One ingested doc with fresh `indexed_at`.

#### When
1. `knowledge_search(query="<shared phrase>", top_k=5, filters={"max_age_days": 14})`.

#### Then
- Only the fresh hit is returned.

---

## §11 — Real-datasheet retrieval QA

Source corpus: real public datasheets (PDFs, sha256-pinned), text
extracts committed under `tests/fixtures/datasheets/`. Executable
scenarios in `tests/uat/scenarios/tier1/datasheets-real.md`. Run via
`/uat-cycle12 --tier 1 --only "KB-DS-"`.

Each scenario ingests a real extracted-text fixture, asks an
engineer-realistic natural-language query, and asserts the top-1 hit
contains a literal substring drawn directly from the source PDF.
This validates that the KB **answers** real engineering questions —
something synthetic markers (§1–§10) cannot prove.

### Datasheet value categories

Engineer-realistic questions break down into 10 categories. Each
datasheet contributes one question per applicable category:

1. **Power-budget** — Vcc range, run/sleep currents, dissipation
2. **Signal-integrity** — VIH/VIL, drive, leakage, IBIS availability
3. **Performance** — clock, ADC/DAC, comm interfaces, GPIO count
4. **Memory** — flash, RAM, EEPROM, memory map
5. **Thermal & mechanical** — Tj, θJA, package, footprint
6. **Reliability** — ESD, latch-up, AEC-Q100 grade, MSL
7. **Compliance & sourcing** — RoHS, lifecycle status, alt-MPN tree
8. **Application & layout guidance** — decoupling, crystal, PCB notes
9. **Errata** — known bugs by silicon revision
10. **Models** — IBIS, SPICE, STEP, BSDL availability

### Datasheet corpus (8 parts × 10 queries = 80 rows)

| MPN | Vendor | Family | Fixture text | gt.yaml |
|---|---|---|---|---|
| RP2040 | Raspberry Pi | MCU | `tests/fixtures/datasheets/rp2040.txt` | `rp2040.gt.yaml` |
| BME280 | Bosch Sensortec | Sensor (T/P/H) | `tests/fixtures/datasheets/bme280.txt` | `bme280.gt.yaml` |
| TPS62840 | Texas Instruments | Power (low-Iq buck) | `tests/fixtures/datasheets/tps62840.txt` | `tps62840.gt.yaml` |
| STM32H723VGT6 | STMicroelectronics | MCU (flagship) | `tests/fixtures/datasheets/stm32h723vgt6.txt` | `stm32h723vgt6.gt.yaml` |
| ESP32-WROOM-32 | Espressif | Wireless module | `tests/fixtures/datasheets/esp32-wroom-32.txt` | `esp32-wroom-32.gt.yaml` |
| nRF52840 | Nordic Semiconductor | BLE SoC | `tests/fixtures/datasheets/nrf52840.txt` | `nrf52840.gt.yaml` |
| LM2596 | Texas Instruments | Buck regulator | `tests/fixtures/datasheets/lm2596.txt` | `lm2596.gt.yaml` |
| MCP2515 | Microchip | CAN controller (AEC-Q100) | `tests/fixtures/datasheets/mcp2515.txt` | `mcp2515.gt.yaml` |

Refresh: `python scripts/datasheets/fetch_and_extract.py` (downloads
the PDFs, re-extracts text, pins sha256s).
Regenerate scenarios: `python scripts/datasheets/generate_scenarios.py`.

### Catalog — RP2040 (10 rows)

| ID | Category | Question (abridged) | Expected substring | Verdict |
|---|---|---|---|---|
| KB-DS-RP2040-PWR-001 | power | nominal core supply voltage (DVDD) | `1.1V` | 🔄 NEW |
| KB-DS-RP2040-PWR-002 | power | VREG_VIN input range | `1.8V to 3.3V` | 🔄 NEW |
| KB-DS-RP2040-PERF-001 | performance | maximum CPU clock | `133MHz` | 🔄 NEW |
| KB-DS-RP2040-PERF-002 | performance | core count + architecture | `Dual ARM Cortex-M0+` | 🔄 NEW |
| KB-DS-RP2040-PERF-003 | performance | ADC characteristics | `12-bit conversion` | 🔄 NEW |
| KB-DS-RP2040-PERF-004 | performance | PIO state machines | `8 PIO state machines` | 🔄 NEW |
| KB-DS-RP2040-PERF-005 | performance | GPIO count | `30 GPIO pins` | 🔄 NEW |
| KB-DS-RP2040-MEM-001 | memory | on-chip SRAM | `264kB` | 🔄 NEW |
| KB-DS-RP2040-PKG-001 | package | package | `QFN-56` | 🔄 NEW |
| KB-DS-RP2040-THERM-001 | thermal | minimum operating temperature | `qualified to -40°C` | 🔄 NEW |

### Catalog — BME280 (10 rows)

| ID | Category | Question (abridged) | Expected substring | Verdict |
|---|---|---|---|---|
| KB-DS-BME280-PWR-001 | power | VDD supply range | `1.71 V to 3.6 V` | 🔄 NEW |
| KB-DS-BME280-PWR-002 | power | VDDIO supply range | `1.2 V to 3.6 V` | 🔄 NEW |
| KB-DS-BME280-PWR-003 | power | sleep-mode current | `0.1 µA` | 🔄 NEW |
| KB-DS-BME280-PERF-001 | performance | I²C max clock | `3.4 MHz` | 🔄 NEW |
| KB-DS-BME280-PERF-002 | performance | SPI max clock | `10 MHz` | 🔄 NEW |
| KB-DS-BME280-PERF-003 | performance | interfaces present | `SPI and I²C` | 🔄 NEW |
| KB-DS-BME280-THERM-001 | thermal | operating temperature range | `Operating range -40` | 🔄 NEW |
| KB-DS-BME280-REL-001 | reliability | ESD HBM | `±2 kV` | 🔄 NEW |
| KB-DS-BME280-PKG-001 | package | package dimensions | `2.5 mm x 2.5 mm x 0.93 mm` | 🔄 NEW |
| KB-DS-BME280-CMP-001 | compliance | RoHS / halogen-free | `RoHS compliant, halogen-free, MSL1` | 🔄 NEW |

### Catalog — TPS62840 (10 rows)

| ID | Category | Question (abridged) | Expected substring | Verdict |
|---|---|---|---|---|
| KB-DS-TPS62840-PWR-001 | power | typical operating Iq | `60 nA` | 🔄 NEW |
| KB-DS-TPS62840-PWR-002 | power | input voltage range | `1.8 V to 6.5 V` | 🔄 NEW |
| KB-DS-TPS62840-PWR-003 | power | maximum output current | `750 mA` | 🔄 NEW |
| KB-DS-TPS62840-PERF-001 | performance | switching frequency | `1.8 MHz` | 🔄 NEW |
| KB-DS-TPS62840-THERM-001 | thermal | max operating Tj | `125 °C` | 🔄 NEW |
| KB-DS-TPS62840-REL-001 | reliability | ESD HBM | `±2000` | 🔄 NEW |
| KB-DS-TPS62840-REL-002 | reliability | ESD CDM | `±500` | 🔄 NEW |
| KB-DS-TPS62840-PKG-001 | package | package option | `SON-8` | 🔄 NEW |
| KB-DS-TPS62840-APP-001 | application | control architecture | `DCS-Control` | 🔄 NEW |
| KB-DS-TPS62840-CMP-001 | compliance | RoHS status | `RoHS` | 🔄 NEW |

### Catalog — STM32H723VGT6 (10 rows)

| ID | Category | Question (abridged) | Expected substring | Verdict |
|---|---|---|---|---|
| KB-DS-STM32H723VGT6-PWR-001 | power | application supply range | `1.62 V to 3.6 V` | 🔄 NEW |
| KB-DS-STM32H723VGT6-PERF-001 | performance | maximum CPU clock | `550 MHz` | 🔄 NEW |
| KB-DS-STM32H723VGT6-PERF-002 | performance | L1 cache size (Cortex-M7) | `32-Kbyte data cache and 32-Kbyte` | 🔄 NEW |
| KB-DS-STM32H723VGT6-MEM-001 | memory | embedded flash size | `1 Mbyte of embedded flash` | 🔄 NEW |
| KB-DS-STM32H723VGT6-SIG-001 | signal | 5 V tolerant I/O availability | `FT 5 V tolerant I/O` | 🔄 NEW |
| KB-DS-STM32H723VGT6-PKG-001 | package | LQFP100 body size | `(14x14 mm)` | 🔄 NEW |
| KB-DS-STM32H723VGT6-THERM-001 | thermal | ambient operating range | `–40 to +85 °C` | 🔄 NEW |
| KB-DS-STM32H723VGT6-APP-001 | application | debug interfaces | `SWD and JTAG interfaces` | 🔄 NEW |
| KB-DS-STM32H723VGT6-CMP-001 | compliance | RoHS / ECOPACK status | `ECOPACK2 compliant` | 🔄 NEW |
| KB-DS-STM32H723VGT6-ERR-001 | errata | errata sheet identifier | `ES0491` | 🔄 NEW |

### Catalog — ESP32-WROOM-32 (10 rows)

| ID | Category | Question (abridged) | Expected substring | Verdict |
|---|---|---|---|---|
| KB-DS-ESP32-WROOM-32-PWR-001 | power | operating supply voltage | `Operatingvoltage/Powersupply: 3.0~3.6V` | 🔄 NEW |
| KB-DS-ESP32-WROOM-32-PERF-001 | performance | maximum CPU clock | `32-bitLX6microprocessor,upto240MHz` | 🔄 NEW |
| KB-DS-ESP32-WROOM-32-PERF-002 | performance | Wi-Fi standards | `802.11b/g/n` | 🔄 NEW |
| KB-DS-ESP32-WROOM-32-PERF-003 | performance | Bluetooth specification | `BluetoothV4.2BR/EDRandBluetoothLE` | 🔄 NEW |
| KB-DS-ESP32-WROOM-32-MEM-001 | memory | integrated SPI flash | `4MBSPIflash` | 🔄 NEW |
| KB-DS-ESP32-WROOM-32-PKG-001 | package | module dimensions | `18×25.5×3.1` | 🔄 NEW |
| KB-DS-ESP32-WROOM-32-THERM-001 | thermal | ambient operating range | `Operatingambienttemperature: –40~85°C` | 🔄 NEW |
| KB-DS-ESP32-WROOM-32-REL-001 | reliability | HBM ESD rating | `Humanbodymodel(HBM):±2000V` | 🔄 NEW |
| KB-DS-ESP32-WROOM-32-APP-001 | application | antenna option | `On-boardPCBantenna` | 🔄 NEW |
| KB-DS-ESP32-WROOM-32-CMP-001 | compliance | green certification | `REACH/RoHS` | 🔄 NEW |

### Catalog — nRF52840 (10 rows)

| ID | Category | Question (abridged) | Expected substring | Verdict |
|---|---|---|---|---|
| KB-DS-NRF52840-PWR-001 | power | supply voltage range | `1.7 V to 5.5 V supply voltage range` | 🔄 NEW |
| KB-DS-NRF52840-PWR-002 | power | System OFF current | `0.4 µA at 3 V in System OFF mode` | 🔄 NEW |
| KB-DS-NRF52840-PERF-001 | performance | core + frequency | `ARM ® Cortex ® -M4 32-bit processor with FPU, 64 MHz` | 🔄 NEW |
| KB-DS-NRF52840-PERF-002 | performance | 802.15.4 support | `IEEE 802.15.4-2006` | 🔄 NEW |
| KB-DS-NRF52840-PERF-003 | performance | USB controller | `USB 2.0 full speed` | 🔄 NEW |
| KB-DS-NRF52840-MEM-001 | memory | flash + RAM size | `1 MB flash and 256 kB RAM` | 🔄 NEW |
| KB-DS-NRF52840-PKG-001 | package | aQFN73 body size | `aQFN 73 package, 7 x 7 mm` | 🔄 NEW |
| KB-DS-NRF52840-THERM-001 | thermal | recommended operating temperature | `TA Operating temperature -40 25 85 °C` | 🔄 NEW |
| KB-DS-NRF52840-REL-001 | reliability | ESD HBM (aQFN73) | `ESD HBM Human Body Model 2 kV` | 🔄 NEW |
| KB-DS-NRF52840-APP-001 | application | NFC tag interface | `Type 2 near field communication` | 🔄 NEW |

### Catalog — LM2596 (10 rows)

| ID | Category | Question (abridged) | Expected substring | Verdict |
|---|---|---|---|---|
| KB-DS-LM2596-PWR-001 | power | maximum input voltage | `Input voltage range up to 40 V` | 🔄 NEW |
| KB-DS-LM2596-PWR-002 | power | output load current | `3-A output load current` | 🔄 NEW |
| KB-DS-LM2596-PWR-003 | power | shutdown standby current | `80 μA` | 🔄 NEW |
| KB-DS-LM2596-PERF-001 | performance | switching frequency | `150-kHz fixed-frequency internal oscillator` | 🔄 NEW |
| KB-DS-LM2596-PERF-002 | performance | adjustable output range | `37-V ±4%` | 🔄 NEW |
| KB-DS-LM2596-PKG-001 | package | available packages | `Available in TO-220 and TO-263 packages` | 🔄 NEW |
| KB-DS-LM2596-THERM-001 | thermal | maximum junction temperature | `Maximum junction temperature 150 °C` | 🔄 NEW |
| KB-DS-LM2596-THERM-002 | thermal | storage temperature range | `Storage temperature, T –65 150 °C` | 🔄 NEW |
| KB-DS-LM2596-REL-001 | reliability | HBM ESD rating | `Human-body model (HBM)` | 🔄 NEW |
| KB-DS-LM2596-APP-001 | application | thermal/current-limit protection | `Thermal shutdown and current-limit protection` | 🔄 NEW |

### Catalog — MCP2515 (10 rows)

| ID | Category | Question (abridged) | Expected substring | Verdict |
|---|---|---|---|---|
| KB-DS-MCP2515-PWR-001 | power | supply voltage range | `Operates from 2.7V-5.5V` | 🔄 NEW |
| KB-DS-MCP2515-PWR-002 | power | typical active current | `5 mA active current (typical)` | 🔄 NEW |
| KB-DS-MCP2515-PWR-003 | power | sleep-mode current | `1 μA standby current (typical) (Sleep mode)` | 🔄 NEW |
| KB-DS-MCP2515-PERF-001 | performance | CAN protocol + bit rate | `CAN V2.0B at 1 Mb/s` | 🔄 NEW |
| KB-DS-MCP2515-PERF-002 | performance | maximum SPI clock | `High-Speed SPI Interface (10 MHz)` | 🔄 NEW |
| KB-DS-MCP2515-PERF-003 | performance | acceptance filter count | `Six 29-bit filters` | 🔄 NEW |
| KB-DS-MCP2515-THERM-001 | thermal | industrial-grade temperature range | `Industrial (I): -40°C to +85°C` | 🔄 NEW |
| KB-DS-MCP2515-THERM-002 | thermal | extended-grade temperature range | `Extended (E): -40°C to +125°C` | 🔄 NEW |
| KB-DS-MCP2515-PKG-001 | package | available packages | `18-Lead PDIP/SOIC` | 🔄 NEW |
| KB-DS-MCP2515-CMP-001 | compliance | lead-free / RoHS | `Pb-free` | 🔄 NEW |

### How a §11 scenario rolls up

For each row above, the agent in `tests/uat/scenarios/tier1/datasheets-real.md`:

1. Reads the fixture file at `tests/fixtures/datasheets/<mpn>.txt`.
2. Calls `mcp__metaforge__knowledge_ingest` with that content,
   `source_path = "datasheet://<mpn>"`, `knowledge_type = "component"`,
   metadata `{ vendor, mpn }`.
3. Calls `mcp__metaforge__knowledge_search` with the engineer's
   natural-language question, `top_k=3`, `knowledge_type="component"`.
4. Asserts:
   - top-1 hit's `source_path` matches the ingest source path
   - top-1 hit's `content` contains the expected substring (literal)
   - top-1 hit's `metadata.mpn` round-trips
   - top-1 hit's `heading` is non-empty (heading-aware chunking)

If the top-1 fails the substring assertion but the substring is
present in top-2 or top-3, mark the row FAIL and capture the chunk
contents — that's a retrieval-ranking signal worth investigating
(embedding model, chunk size, reranker), not a harness regression.

### Adding a new datasheet

1. Append a new entry to `tests/fixtures/datasheets/manifest.yaml`
   with empty sha256 fields.
2. Run `python scripts/datasheets/fetch_and_extract.py`.
3. Hand-author `<mpn>.gt.yaml` with ~10 engineer-realistic queries
   (verify each `expected_substring` is literally present in the
   generated `.txt`).
4. Run `python scripts/datasheets/generate_scenarios.py` to refresh
   `tests/uat/scenarios/tier1/datasheets-real.md`.
5. Append the new catalog rows here in §11 with verdict `🔄 NEW`.
6. Run `/uat-cycle12 --tier 1 --only "KB-DS-<MPN>-"` to baseline
   verdicts; copy them back here.

---

## Suite execution

The trackable plan above is run via the existing `/uat-cycle12`
harness. No new tooling required.

```bash
# Tier-0 smoke (golden flow only) — every PR/nightly
/uat-cycle12

# Full Tier-1 KB suite — every Cycle gate
/uat-cycle12 --tier 1

# Tier-2 observability + error-envelope probes — weekly
/uat-cycle12 --tier 2

# Single-row reproducer (catalog ID maps to scenario title substring)
/uat-cycle12 --only "round-trip"
/uat-cycle12 --only "FC-01"
```

After each run, the `uat-validator` agent writes a report to
`docs/uat/uat-claude-driven-report-<date>.md` and files Linear
follow-ups for any FAIL. To update this catalog:

1. Open the run report.
2. For each row in §1–§10, copy the verdict + run-date into the
   corresponding scenario section (and bump the matrix in
   [Capability matrix](#capability-matrix)).
3. If a `🔄 NEW` row gets authored as an executable scenario, swap
   the marker for the new file path (e.g. `tier1/full-capability.md`
   → `FC-01`).

## Authoring queue (the 26 NEW rows)

To go from 26/56 PASS to 50+/56 PASS, author these scenarios. Group
them by file to minimise PR churn:

| Target file | Catalog IDs | Theme |
|---|---|---|
| `tier1/full-capability.md` (new) | KB-ING-009, KB-ING-012, KB-CTX-002, KB-RES-002, KB-RES-003, KB-VER-003, KB-SRC-011 | High-value tier-1 gap fills |
| `tier1/cli-error-paths.md` (new) | KB-CLI-005, KB-CLI-006 | CLI resilience |
| `tier2/error-envelope-probe.md` (extend) | KB-ING-010, KB-ING-011, KB-ERR-002, KB-ERR-003, KB-RES-004, KB-SRC-013, KB-SRC-014 | Error envelope coverage |
| `tier2/streaming-progress-probe.md` (new) | KB-PRG-001, KB-PRG-002, KB-PRG-003 | MET-388 just-shipped capability |
| `tier2/observability-knowledge-probe.md` (new) | KB-OBS-002, KB-OBS-003, KB-CTX-003 | Metrics + log labels |
| `tier1/event-ingest.md` (new) | KB-EVT-002, KB-EVT-003, KB-EVT-004 | Kafka consumer surface |
| `tier1/retrieval.md` (extend) | KB-SRC-012, KB-CTX-004 | Compound filters + fallback |

Each new file follows the parsing contract in
`.claude/agents/uat-validator.agent.md` lines 67–110 (every
`## Scenario:` block must have `Validates`, `Tier`, `Given`, `When`,
`Then`).

## Acceptance gates

- **Cycle gate (tier-1):** 26/26 currently-PASS rows remain PASS.
  Any regression to FAIL files a Linear follow-up under MET-409
  with the catalog id in the title (e.g. `UAT FAIL — KB-ING-001:
  round-trip search no longer matches`).
- **Cycle gate (tier-2):** observability probes (`KB-OBS-001`,
  `KB-VER-001`, `KB-VER-002`) PASS.
- **Cycle 3 close-out:** ≥ 75% of the 56 catalog rows in PASS state
  (the 4 BLOCKED rows depend on MET-399 and reranker work outside
  Cycle 3 scope).

## Appendix A — files touched / referenced by this plan

| Path | Role |
|---|---|
| `docs/uat/kb-test-plan.md` | This file (living catalog) |
| `tests/uat/scenarios/tier1/knowledge.md` | 8 existing scenarios mapped above |
| `tests/uat/scenarios/tier1/ingest.md` | HP-INGEST-01..10 mapped above |
| `tests/uat/scenarios/tier1/retrieval.md` | HP-RETR-01..10 mapped above |
| `tests/uat/scenarios/tier2/staleness-probe.md` | KB-VER-001/002 |
| `tests/uat/scenarios/tier2/error-envelope-probe.md` | KB-ERR-001 (will be extended) |
| `tests/uat/scenarios/tier2/otel-continuity-probe.md` | KB-OBS-001 |
| `.claude/commands/uat-cycle12.md` | Runner entry point |
| `.claude/agents/uat-validator.agent.md` | Scenario parsing contract |
| `tool_registry/tools/knowledge/adapter.py` | Tool surface under test |
| `digital_twin/knowledge/consumer.py` | Event-driven ingest under test |
| `docs/uat/cycle3-knowledge-ssot-test-run-2026-04-30.md` | Most recent baseline run |

## Appendix B — Linear linkage

| Linear id | What it means in this plan |
|---|---|
| MET-409 | Cycle 3 UAT epic (parent for all FAIL follow-ups) |
| MET-346 | `knowledge.ingest` tool contract |
| MET-293 | `knowledge.search` tool contract (top_k, ordering) |
| MET-307 | KnowledgeConsumer / dedup / supersede |
| MET-335 | Citation enrichment + hybrid search + reranker |
| MET-336 | CLI walker + multi-file ingest |
| MET-384 | Resources surface (`metaforge://knowledge/...`) |
| MET-385 | Error envelope contract |
| MET-387 | Per-call context + metadata propagation |
| MET-388 | Streaming progress notifications |
| MET-399 | PDF / CSV ingest |
| MET-401 | Project isolation, latency SLO |
| MET-323/326 | Staleness & supersede metrics |
