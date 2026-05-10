# L1 (Knowledge Layer) — Loop-Driven Implementation Plan

> **Goal.** Implement every L1 backlog item so `/uat-cycle12 --tier 1`
> covers the full L1 surface end-to-end and the master test plan
> (`docs/uat/kb-test-plan.md`) reaches ≥ 75 / 86 PASS.
>
> **Owner of execution.** `/loop /implement-l1-next` (self-paced). Each
> loop iteration picks the next unblocked item from the [Status board](#status-board),
> implements it per its [spec](#items), opens a PR, marks status, and
> exits. The user reviews and merges; the next iteration starts.
>
> **Authority.** This file is the single source of truth for the
> implementation. Spec changes here = behavior changes in the loop.
> The loop must NOT invent items; if the spec is unclear it marks
> `⏸️ blocked: clarification` and exits.

---

## How the loop consumes this file

Every loop iteration runs `/implement-l1-next`. The slash command
splits the work between an **outer context** (planning + dispatch) and
an **inner sub-agent** (implementation), so context does not carry
across iterations.

**Outer context** (`/implement-l1-next` itself):

1. **Pre-flight** — clean tree, on `main`, baseline tests collect, gh auth.
2. **Reads** the [Status board](#status-board) below.
3. **Picks** the topmost row where `Status == ⏳ Pending` AND every
   id in `Deps` has `Status == ✅`. If no such row exists, the loop
   stops with a final report.
4. **Aborts and marks `⏸️`** if the item carries
   `Requires human decision: true`. Reports which decision and stops.
5. **Creates branch** `feat/met-<id>-<short-slug>` from `main`.
6. **Spawns a sub-agent** (Agent tool, `subagent_type: general-purpose`,
   fresh context) with the item's spec, files-to-touch, tests, and
   acceptance criteria. The sub-agent does the actual work.
7. **Receives the sub-agent's structured JSON result** and updates the
   row's `Status` (✅ / ❌ / ⏸️), `Branch`, `PR` columns. Commits the
   plan-file update onto the same branch and pushes.
8. **Exits.** The user reviews and merges; the next iteration starts
   in a fresh outer context (and another fresh inner sub-agent).

**Inner sub-agent** (one per iteration, fresh context):

- Receives the spec + files + tests + acceptance verbatim.
- Implements per spec, **no scope creep** — if forced to expand, returns
  `BLOCKED_CLARIFICATION` instead of silently growing the diff.
- Runs the validation gauntlet: `pytest <test paths>`, `ruff check`,
  `mypy` on touched paths. Retries up to 3× on failure.
- Commits with conventional-commits message + MET id in body.
- Pushes, opens PR (`gh pr create`).
- Returns a single JSON block (`result`, `pr_url`, `summary`,
  `files_touched`, `tests_added`, `validation`) for the outer context
  to act on.

**Why fresh context per item**

- Each iteration's diff is reproducible from the spec alone — no
  hidden carry-over from previous items.
- A confused or off-track sub-agent in iteration N can't pollute N+1.
- The outer context stays small (item selection + plan edits only),
  so the loop runs many iterations without bloating its window.

The exact sub-agent prompt template + return contract live in
`.claude/commands/implement-l1-next.md`. Edit that file to change how
the work gets done; edit this file to change *what* the work is.

### Stop conditions

- All rows are `✅` or `⏸️` → loop reports "L1 done" and stops.
- Three consecutive iterations end in `❌` or `⏸️` → loop reports
  "stalled" and stops.
- 8 hours of wall-clock since loop start → loop reports "time-boxed"
  and stops.

### Conventions enforced by every iteration

- **Never merge to main locally.** Every change ships as a PR.
- **Conventional commits.** `feat(twin-core): …`, `fix(knowledge): …`,
  `test(uat): …`. Linear id is mandatory.
- **No new dependencies** without an item that explicitly authorises
  it. (E.g., L1-A2 authorises adding a reranker model; nothing else
  may add deps without a spec amendment.)
- **No edits to production data layout** (pgvector dim, namespace
  prefix) outside L1-A5 / L1-D1, which carry
  `Requires human decision: true`.
- **Observability rule (CLAUDE.md).** Every new public function gets
  a structlog log line, a metric, and a tracer span — even if the
  spec doesn't repeat that.
- **WSL2 polling, never inotify** for any file watcher work.

---

## Status board

The loop edits this table in place. **Manual edits are allowed when
adjusting priorities or unblocking dependencies — the loop preserves
columns it didn't update.**

Status legend: ⏳ pending · 🔧 in progress · ✅ done (PR open) ·
⏸️ blocked (clarification / decision / infra) · ❌ failed (3 retries) ·
🚫 cancelled.

| ID | Title | MET | Effort | Deps | Status | Branch | PR | Notes |
|---|---|---|---|---|---|---|---|---|
| L1-A1 | Project isolation enforcement | MET-401 | M | — | ✅ | feat/met-401-knowledge-project-isolation | [#163](https://github.com/FidelOdok/MetaForge/pull/163) | 13 passed, 2 skipped (no PG); minor: also touched test_context_assembler.py |
| L1-A2 | Hybrid-search reranker | MET-335 | M | — | ✅ | feat/met-335-knowledge-reranker | [#164](https://github.com/FidelOdok/MetaForge/pull/164) | 20 passed; mypy: 2 pre-existing errors on api_gateway/server.py noted, not introduced |
| L1-A3 | PDF parser shipped | MET-399 | S | — | ✅ | feat/met-399-pdf-ingest-wiring | [#165](https://github.com/FidelOdok/MetaForge/pull/165) | wired pdfplumber path; HP-INGEST-03 promoted; 20 passed, 8 skipped (no PG) |
| L1-A4 | CSV row-level chunker | MET-340 | M | — | ✅ | feat/met-340-csv-row-chunker | [#166](https://github.com/FidelOdok/MetaForge/pull/166) | chunk_csv() + ingest detection; 8 unit tests + 28 passed |
| L1-A5 | Workspace separation enforced | MET-346 | M | — | ✅ | (policy pin) | — | Pinned: keep gateway and LightRAG-UI workspaces SEPARATE per ADR-010 Phase-1 (engineer dogfood). Already enforced in code (gateway uses `lightrag` namespace; UI uses `lightrag_ui`). Phase-2 integration via L1-E2 instead. |
| L1-A6 | Re-ingest after edit retires stale | MET-307 | S | — | ✅ | feat/met-307-supersede-on-edit | [#167](https://github.com/FidelOdok/MetaForge/pull/167) | sha256-based supersede; 28 passed, 11 skipped (no PG) |
| L1-A7 | Latency SLO instrumentation | MET-401 | S | L1-D2 | ✅ | feat/met-401-knowledge-latency-slo | [#194](https://github.com/FidelOdok/MetaForge/pull/194) | metaforge_knowledge_search_duration_seconds + p95 alert; 7 tests; metric prefix pinned per platform convention |
| L1-A8 | `list_sources()` Protocol method | MET-415 | S | — | ✅ | feat/met-415-list-sources | [#168](https://github.com/FidelOdok/MetaForge/pull/168) | 11 unit tests; SQL targets lightrag_vdb_chunks (real LightRAG storage) |
| L1-B1 | `metaforge://knowledge/sources` resource | MET-384 | S | L1-A8 | ✅ | feat/met-384-knowledge-resources | [#169](https://github.com/FidelOdok/MetaForge/pull/169) | both URIs registered; 6 unit tests + 53 passed |
| L1-B2 | Streaming progress on multi-file ingest | MET-388 | M | — | ✅ | feat/met-388-knowledge-progress | [#170](https://github.com/FidelOdok/MetaForge/pull/170) | multi-file batch + supports_progress on ToolManifest; 6 tests; 246 passed broader |
| L1-B3 | Per-call context propagation | MET-387 | S | — | ✅ | feat/met-387-context-propagation | [#171](https://github.com/FidelOdok/MetaForge/pull/171) | actor_id forwarding + OTel span attrs; 7 tests, 60 passed |
| L1-B4 | Malformed knowledge_type → MET-385 envelope | MET-385 | XS | — | ✅ | fix/met-385-knowledge-type-envelope | [#172](https://github.com/FidelOdok/MetaForge/pull/172) | enum validation at adapter layer; 13 tests, 65 passed |
| L1-B5 | Compound filter semantics pinned | MET-417 | XS | — | ✅ | feat/met-417-compound-filters | [#173](https://github.com/FidelOdok/MetaForge/pull/173) | AND-across-keys equality; str/int/bool/None values; dict/list rejected; 10 tests, 75 passed |
| L1-C1 | `forge sources list/show/delete` | MET-411 | S | L1-A8, L1-B1 | ✅ | feat/met-411-forge-sources-cli | [#174](https://github.com/FidelOdok/MetaForge/pull/174) | CLI + REST passthrough; 20 new tests, 240 forge/knowledge passing |
| L1-C2 | CLI error reporting on bad input | MET-411 | XS | — | ✅ | fix/met-411-forge-ingest-errors | [#175](https://github.com/FidelOdok/MetaForge/pull/175) | nonexistent/empty/binary/permission paths handled; 8 new tests, 101 passed |
| L1-C3 | CLI PDF walker integration | MET-399 | XS | L1-A3 | ✅ | feat/met-399-cli-pdf-walker | [#176](https://github.com/FidelOdok/MetaForge/pull/176) | 5 integration tests; sub-agent stalled at PR-create step, outer-context recovery |
| L1-D1 | Phase-1 UI shared-workspace alignment | MET-346 | S | — | ✅ | (policy pin) | — | Pinned: matches L1-A5 — Phase-1 stays SEPARATE; LightRAG-UI uses Ollama (default), gateway uses sentence-transformers (default). Cross-workspace bridging deferred to Phase-2. |
| L1-D2 | MET-346 adoption checklist green | MET-346 | S | L1-D1 | ✅ | chore/met-346-adoption-checklist | [#192](https://github.com/FidelOdok/MetaForge/pull/192) | 5 items marked [x] with evidence pointers; run report committed |
| L1-D3 | Phase-1 docs final pass | MET-346 | XS | L1-D2 | ⏳ | — | — | docs/integrations/lightrag-ui.md |
| L1-E1 | ADR-010 Phase-2 spec finalized | new | S | — | ✅ | (policy pin) | — | Pinned v1 scope: SOURCES TABLE only (L1-E2). Search bar (L1-E3) and graph embed (L1-E4) deferred to v2. Sidebar relabel (L1-E5) included. Drill-in deferred. |
| L1-E2 | `/knowledge` page — sources table | new | M | L1-B1, L1-E1 | ✅ | feat/met-409-knowledge-sources-page | [#193](https://github.com/FidelOdok/MetaForge/pull/193) | 4 vitest specs; tsc PASS; sort + chip filter + empty state + drill-in stub |
| L1-E3 | `/knowledge` page — search bar | new | M | L1-E2 | ⏳ | — | — | citations + snippet highlight |
| L1-E4 | `/knowledge` page — graph embed | new | L | L1-E2, L1-B1 | ⏳ | — | — | Sigma.js, multi-iteration allowed |
| L1-E5 | Sidebar relabel | new | XS | L1-E2 | ⏳ | — | — | Knowledge → /knowledge; Files → /files |
| L1-F1 | Author 26 NEW kb-test-plan rows | MET-410 | L | — | ✅ | (parent of F1a–F1g) | (PRs #177, #178, #179, #180, #181, #182, #183) | all 7 sub-iterations merged |
| L1-F1a | full-capability.md (7 scenarios) | MET-410 | S | — | ✅ | test/met-410-full-capability | [#177](https://github.com/FidelOdok/MetaForge/pull/177) | 7 scenarios; master plan Verdicts flipped for 4 already-implemented rows |
| L1-F1b | cli-error-paths.md (2 scenarios) | MET-410 | XS | — | ✅ | test/met-410-cli-errors | [#178](https://github.com/FidelOdok/MetaForge/pull/178) | KB-CLI-005, KB-CLI-006 already ✅ from L1-C2; just adds executable scenarios |
| L1-F1c | error-envelope-probe.md extend (7) | MET-410 | S | — | ✅ | test/met-410-error-envelope-extend | [#179](https://github.com/FidelOdok/MetaForge/pull/179) | 7 scenarios; 3 Verdicts flipped (KB-ING-010, KB-RES-004, KB-SRC-014); 4 await impl |
| L1-F1d | streaming-progress-probe.md (3) | MET-410 | S | — | ✅ | test/met-410-streaming-progress-probe | [#180](https://github.com/FidelOdok/MetaForge/pull/180) | 3 scenarios; Verdicts flipped (KB-PRG-001..003); all backed by L1-B2 |
| L1-F1e | observability-knowledge-probe.md (3) | MET-410 | S | — | ✅ | test/met-410-observability-knowledge | [#181](https://github.com/FidelOdok/MetaForge/pull/181) | 3 scenarios w/ PromQL+LogQL; KB-CTX-003 flipped ✅; OBS-002/003 await impl |
| L1-F1f | event-ingest.md (3) | MET-410 | S | — | ✅ | test/met-410-event-ingest | [#182](https://github.com/FidelOdok/MetaForge/pull/182) | 3 Kafka-gated scenarios; report BLOCKED if broker absent |
| L1-F1g | retrieval.md extend (2) | MET-410 | XS | — | ✅ | test/met-410-retrieval-extend | [#183](https://github.com/FidelOdok/MetaForge/pull/183) | HP-RETR-11/12; KB-SRC-012, KB-CTX-004 flipped ✅ |
| L1-F2 | Real-content corpus expansion | MET-340 | M | — | ✅ | test/met-340-corpus-expansion | [#184](https://github.com/FidelOdok/MetaForge/pull/184) | 8 parts, 80 scenarios; STM32H723/ESP32/nRF52840/LM2596/MCP2515 added; 3 URL workarounds (MCP2515 to revJ, nRF52840 via web.archive, STM32 via curl headers) |
| L1-F3 | Dual-project isolation integration test | MET-401 | S | L1-A1 | ✅ | test/met-401-project-isolation + fix/met-401-project-scoped-delete-supersede | [#186](https://github.com/FidelOdok/MetaForge/pull/186) + [#190](https://github.com/FidelOdok/MetaForge/pull/190) | 4 tests; #190 also ships the production fix that #186's sub-agent identified |
| L1-F4 | Citation round-trip integration test | MET-389 | S | — | ✅ | test/met-389-citation-roundtrip | [#188](https://github.com/FidelOdok/MetaForge/pull/188) | 4 cases (h2/h1-h2/chunk_index/metadata); 13 passed, 12 skipped (no PG) |
| L1-F5 | Neo4j ↔ in-memory parity test | new | S | — | ⏳ | — | — | KB queries |

**Total: 29 items.** 3 blocked on human decision (L1-A5, L1-D1, L1-E1).
**Loop-eligible from kickoff: 26 items.**

---

## Items

Each block below is the loop's per-item contract. Everything outside
"Files", "Spec", "Tests", and "Done when" is context for the human
reader.

### L1-A1 — Project isolation enforcement

**MET:** MET-401 · **Effort:** M · **Branch:** `feat/met-401-knowledge-project-isolation`

**Background.** TODO at `digital_twin/knowledge/lightrag_service.py:235–276`
notes that `project_id` is captured but not used to scope ingest or
search. Without scoping, project A's docs leak into project B searches.

**Files:**
- `digital_twin/knowledge/lightrag_service.py` (ingest, search)
- `digital_twin/knowledge/service.py` (Protocol — add `project_id` arg if missing)
- `tool_registry/tools/knowledge/adapter.py` (forward `project_id` from `current_context()`)
- `tests/integration/test_knowledge_project_isolation.py` (new)

**Spec:**
1. In `LightRAGKnowledgeService.ingest`, accept `project_id: UUID | None`
   and stamp it into the chunk metadata under `metadata.project_id`.
2. In `LightRAGKnowledgeService.search`, accept `project_id: UUID | None`.
   When set, add a metadata filter `WHERE metadata->>'project_id' = $project`
   to the underlying query. When `None`, fall back to the documented
   default-tenant behavior (decide and pin: either "search across all"
   or "search the `default` tenant only" — pick the latter for safety).
3. In `tool_registry/tools/knowledge/adapter.py`, read
   `current_context().project_id` and pass it through.
4. Update `KnowledgeService` Protocol at `digital_twin/knowledge/service.py:63`
   to declare the new parameter.

**Tests:**
- `tests/integration/test_knowledge_project_isolation.py`:
  1. Ingest content X under `project_id=P_A`.
  2. Search same query under `project_id=P_A` — assert ≥ 1 hit.
  3. Search same query under `project_id=P_B` — assert 0 hits.
- Update `tests/unit/test_knowledge_mcp_adapter.py` to assert the
  adapter forwards `project_id` from context.

**Done when:**
- New integration test PASS.
- Existing tests still PASS.
- TODO comment at `lightrag_service.py:235–276` removed.
- PR title: `feat(knowledge): project isolation enforcement (MET-401)`.

---

### L1-A2 — Hybrid-search reranker

**MET:** MET-335 · **Effort:** M · **Branch:** `feat/met-335-knowledge-reranker`

**Background.** HP-RETR-06 in `tier1/retrieval.md` asserts the reranker
improves the top result; today it's marked SKIP because no reranker is
wired.

**Files:**
- `pyproject.toml` — add `sentence-transformers>=2.0` if absent (already in `[knowledge]`); add `bge-reranker-base` model is downloaded at runtime, not a pip dep.
- `digital_twin/knowledge/reranker.py` (new — small wrapper around `CrossEncoder`)
- `digital_twin/knowledge/lightrag_service.py` — call reranker after vector retrieval if enabled
- `digital_twin/knowledge/service.py` — add `rerank: bool = False` to `search()` signature
- `tests/unit/test_knowledge_reranker.py` (new)

**Spec:**
1. New `Reranker` class wrapping `CrossEncoder("BAAI/bge-reranker-base")`
   with `async def rerank(query: str, hits: list[Hit]) -> list[Hit]`
   that returns hits re-ordered by cross-encoder score.
2. `LightRAGKnowledgeService.search` accepts `rerank: bool = False`.
   When True, runs vector retrieval with `top_k * 3` candidates,
   passes them through `Reranker.rerank`, then truncates to `top_k`.
3. Reranker is lazily instantiated on first use (model download is ~440MB).
4. Add env var `KNOWLEDGE_RERANKER_ENABLED=false` default; gateway
   reads it at init and passes to service.

**Tests:**
- `tests/unit/test_knowledge_reranker.py`: load a fixture corpus where
  one chunk is technically more relevant but has lower cosine; verify
  the reranker promotes it.
- `tier1/retrieval.md` HP-RETR-06: change SKIP → PASS.

**Done when:**
- Unit test PASS.
- HP-RETR-06 updated to executable PASS-on-rerank-enabled environment.
- PR title: `feat(knowledge): hybrid-search reranker (MET-335)`.

---

### L1-A3 — PDF parser wiring verified

**MET:** MET-399 · **Effort:** S · **Branch:** `feat/met-399-pdf-ingest-wiring`

**Background.** `pyproject.toml` declares `raganything>=1.2` in the
`[knowledge]` extra and the CLI sends raw PDF bytes via latin-1
JSON-encoded payload. KB-CLI-003 / HP-INGEST-03 are BLOCKED today.
Verify the path actually works end-to-end.

**Files:**
- `digital_twin/knowledge/lightrag_service.py` — confirm raganything
  is invoked when content looks like a PDF byte payload; if not, wire it.
- `tests/integration/test_pdf_ingest.py` (new)
- `tests/fixtures/datasheets/` — reuse the existing rp2040.pdf or copy
  one of the `.cache/datasheets/*.pdf` files into a small committed
  fixture (≤ 200 KB; truncate if needed).

**Spec:**
1. End-to-end test ingests a small real PDF (< 200 KB) via
   `knowledge_ingest`, then searches for a known-present phrase.
2. Verify `chunks_indexed > 1` (multi-page chunking).
3. Verify hit's citation includes a `Page N` heading or recovered
   section title.

**Tests:**
- `tests/integration/test_pdf_ingest.py` covering ingest + search.
- HP-INGEST-03 in `tier1/ingest.md` updated from BLOCKED → executable.

**Done when:**
- Integration test PASS.
- KB-CLI-003 in `kb-test-plan.md` updated.
- PR title: `feat(knowledge): verify PDF ingest end-to-end (MET-399)`.

---

### L1-A4 — CSV row-level chunker

**MET:** MET-340 · **Effort:** M · **Branch:** `feat/met-340-csv-row-chunker`

**Background.** HP-INGEST-04 expects each CSV row to become a
searchable chunk so engineers can hit a BOM by MPN. Today CSV ingest
falls back to one-chunk-per-file.

**Files:**
- `digital_twin/knowledge/chunker.py` — add `chunk_csv(content) -> list[Chunk]`
- `digital_twin/knowledge/lightrag_service.py` — branch on file
  extension or content-type to call CSV chunker
- `tests/unit/test_csv_chunker.py` (new)
- `tests/fixtures/knowledge/bom.csv` (new — small synthetic BOM)

**Spec:**
1. Detect CSV by extension `.csv` or by metadata `content_type=text/csv`.
2. First row is treated as header; subsequent rows become individual
   chunks with metadata `{ row_index: N, columns: {col: value} }`.
3. Chunk content is the row formatted as `key=value` pairs joined with
   `; `, e.g. `mpn=STM32H723; package=LQFP100; price=8.50`.
4. Header row included in each chunk's metadata for context.

**Tests:**
- `tests/unit/test_csv_chunker.py`: 5-row CSV → 5 chunks; chunk[2]'s
  metadata.row_index == 2; chunk content contains the MPN from row 2.
- HP-INGEST-04 updated from BLOCKED → executable.

**Done when:**
- Unit test PASS.
- PR title: `feat(knowledge): CSV row-level chunker (MET-340)`.

---

### L1-A5 — Workspace separation enforcement

**Status:** ⏸️ **Requires human decision: true.** Skip until owner pins
direction (shared vs strict isolation between gateway and LightRAG UI).
See `docs/integrations/lightrag-ui.md` and the Phase-1/Phase-2 split.

---

### L1-A6 — Re-ingest after edit retires stale fragments

**MET:** MET-307 · **Effort:** S · **Branch:** `feat/met-307-supersede-on-edit`

**Background.** Today identical re-ingest at the same source_path is
dedup'd (HP-INGEST-06 covers this). When content actually changes —
the engineer edited the file — the old chunks must be retired.

**Files:**
- `digital_twin/knowledge/lightrag_service.py` — `ingest` method
- `tests/integration/test_reingest_after_edit.py` (new)

**Spec:**
1. Before storing new chunks for an existing `source_path`, compute a
   hash over `content`. If the hash is unchanged, dedup as today.
2. If the hash is **different** (engineer edited the file), call
   `delete_by_source(source_path)` first, then ingest fresh chunks.
3. Emit a `knowledge_consumer_predelete` log event with the
   `source_path` and old chunk count for the staleness probe.

**Tests:**
- `tests/integration/test_reingest_after_edit.py`:
  1. Ingest content α at source X.
  2. Ingest content β (different) at source X.
  3. Search for α-specific phrase → 0 hits or below 0.5 similarity.
  4. Search for β-specific phrase → ≥ 1 hit.

**Done when:**
- Integration test PASS.
- KB-ING-009 in `kb-test-plan.md` flips to ✅.
- PR title: `feat(knowledge): supersede stale fragments on edit (MET-307)`.

---

### L1-A7 — Latency SLO instrumentation

**MET:** MET-401 · **Effort:** S · **Deps:** L1-D2 · **Branch:** `feat/met-401-knowledge-latency-slo`

**Background.** HP-RETR-08 wants `p95 < 200 ms` on a 1k-doc corpus.
Today wall-clock is captured but not gated against a Prometheus
histogram.

**Files:**
- `digital_twin/knowledge/lightrag_service.py` — add tracer + histogram instrumentation
- `observability/metrics.py` — register `knowledge_search_duration_seconds` histogram
- `observability/alerting/rules.yaml` — add SLO alert rule
- `tests/unit/test_knowledge_latency_metrics.py` (new)

**Spec:**
1. Wrap `LightRAGKnowledgeService.search` with histogram + tracer span
   (existing `tracer.start_as_current_span("lightrag.search")` is good
   — confirm it records duration as a span attribute and increments
   `knowledge_search_duration_seconds`).
2. Add Prometheus alert: p95 latency > 200 ms over 5 min → page (Sev 3).
3. Update `alerting/rules.yaml`.

**Tests:**
- `tests/unit/test_knowledge_latency_metrics.py`: stub histogram,
  invoke search, assert observation count incremented and value > 0.

**Done when:**
- Unit test PASS.
- Alert rule lands in `observability/alerting/rules.yaml`.
- PR title: `feat(observability): knowledge search latency SLO (MET-401)`.

---

### L1-A8 — `list_sources()` Protocol method

**MET:** MET-415 (file new if missing) · **Effort:** S · **Branch:** `feat/met-415-list-sources`

**Background.** Today `KnowledgeService` exposes ingest/search/delete
but no listing. The `list()` primitive exists on the legacy
`KnowledgeStore` (`digital_twin/knowledge/store.py:471`); we need to
surface it via the Protocol so REST + MCP can call it.

**Files:**
- `digital_twin/knowledge/service.py` — add Protocol method
- `digital_twin/knowledge/lightrag_service.py` — implement against pgvector
- `tests/unit/test_knowledge_service_list.py` (new)

**Spec:**
1. New Protocol method:
   ```python
   async def list_sources(
       self,
       project_id: UUID | None = None,
       knowledge_type: KnowledgeType | None = None,
       limit: int = 100,
       offset: int = 0,
   ) -> list[SourceSummary]: ...
   ```
2. `SourceSummary` model: `{ source_path, knowledge_type, fragment_count, indexed_at, metadata }`.
3. `LightRAGKnowledgeService.list_sources` runs:
   ```sql
   SELECT metadata->>'source_path' AS source_path,
          knowledge_type,
          COUNT(*) AS fragment_count,
          MAX(created_at) AS indexed_at,
          (array_agg(metadata))[1] AS metadata
   FROM knowledge_entries
   WHERE workspace = $1
     AND ($2::uuid IS NULL OR metadata->>'project_id' = $2::text)
     AND ($3 IS NULL OR knowledge_type = $3)
   GROUP BY source_path, knowledge_type
   ORDER BY indexed_at DESC
   LIMIT $4 OFFSET $5;
   ```

**Tests:**
- `tests/unit/test_knowledge_service_list.py`: ingest 3 sources of
  mixed types, list with no filter → 3 rows; with type filter → 1 row;
  fragment_count matches the chunk count from ingest.

**Done when:**
- Unit test PASS.
- PR title: `feat(knowledge): list_sources() Protocol method (MET-415)`.

---

### L1-B1 — `metaforge://knowledge/sources` MCP resource

**MET:** MET-384 · **Effort:** S · **Deps:** L1-A8 · **Branch:** `feat/met-384-knowledge-resources`

**Background.** HP-INGEST-01 already asserts `metaforge://knowledge/sources`
returns a list. KB-RES-001..004 in `kb-test-plan.md` track this. Today
the resource is documented but never registered.

**Files:**
- `tool_registry/tools/knowledge/adapter.py` — call `register_resource(...)`
- `tests/unit/test_knowledge_resources.py` (new)

**Spec:**
1. Register two URIs:
   - `metaforge://knowledge/sources` → reader returns a list summary
   - `metaforge://knowledge/sources/{id}` → reader returns full source
     detail including chunks
2. Reader functions delegate to `KnowledgeService.list_sources()` (L1-A8).
3. Resources advertised in `resources/list` capability response.

**Tests:**
- `tests/unit/test_knowledge_resources.py`: list returns the URI;
  read on each URI returns the documented schema; read on unknown
  source id returns `not_found` envelope (MET-385).

**Done when:**
- Unit test PASS.
- HP-INGEST-01 step 2 PASS against the implementation.
- PR title: `feat(mcp): knowledge sources resource surface (MET-384)`.

---

### L1-B2 — Streaming progress on multi-file ingest

**MET:** MET-388 · **Effort:** M · **Branch:** `feat/met-388-knowledge-progress`

**Background.** MET-388 protocol surface shipped in commit cc0db58 but
the knowledge tool isn't emitting yet.

**Files:**
- `tool_registry/tools/knowledge/adapter.py` — `knowledge_ingest`
  handler emits progress on each file.
- `tests/integration/test_knowledge_streaming_progress.py` (new)

**Spec:**
1. When `knowledge_ingest` is called with a list-of-files payload (or a
   directory walk in CLI mode), emit progress notifications via
   `mcp_core.progress.emit_progress(current, total, message)` after
   each file.
2. Advertise progress capability in `tools/list` for `knowledge_ingest`.

**Tests:**
- `tests/integration/test_knowledge_streaming_progress.py`: ingest 5
  files, capture notifications; assert ≥ 5 progress events received
  before final response, monotonically advancing.

**Done when:**
- Integration test PASS.
- KB-PRG-001..003 flip to ✅ in `kb-test-plan.md`.
- PR title: `feat(mcp): streaming progress on knowledge ingest (MET-388)`.

---

### L1-B3 — Per-call context propagation

**MET:** MET-387 · **Effort:** S · **Branch:** `feat/met-387-context-propagation`

**Background.** `current_context()` already exists at
`mcp_core/context.py:124–162` but the knowledge handlers don't read
it.

**Files:**
- `tool_registry/tools/knowledge/adapter.py`
- `tests/unit/test_knowledge_call_context.py` (new)

**Spec:**
1. In both `knowledge_ingest` and `knowledge_search` handlers, read
   `current_context().project_id` and `current_context().actor_id`,
   pass into `KnowledgeService` calls.
2. Set OTel span attributes `mcp.project_id` and `mcp.actor_id` from
   the context.

**Tests:**
- `tests/unit/test_knowledge_call_context.py`: call with stubbed
  context, assert `KnowledgeService` was invoked with matching
  project_id; assert span has the expected attributes.

**Done when:**
- Unit test PASS.
- KB-CTX-003 / KB-CTX-004 in `kb-test-plan.md` flip to ✅.
- PR title: `feat(mcp): per-call context propagation in knowledge tools (MET-387)`.

---

### L1-B4 — Malformed `knowledge_type` returns MET-385 envelope

**MET:** MET-385 · **Effort:** XS · **Branch:** `fix/met-385-knowledge-type-envelope`

**Files:**
- `tool_registry/tools/knowledge/adapter.py`
- `tests/unit/test_knowledge_tool_errors.py` (extend)

**Spec:**
1. Validate `knowledge_type` against the documented enum at
   request-decode time. On mismatch, raise `McpToolError` with
   `code="invalid_input"`, message listing the allowed values, and
   the rejected value in `data`.

**Tests:**
- Extend `test_knowledge_tool_errors.py`: pass
  `knowledge_type="not_a_real_type"` → assert envelope shape and
  enum-listing message.

**Done when:**
- Unit test PASS.
- KB-ING-010 in `kb-test-plan.md` flips to ✅.
- PR title: `fix(mcp): malformed knowledge_type returns MET-385 envelope`.

---

### L1-B5 — Compound filter semantics pinned

**MET:** MET-417 (file new if missing) · **Effort:** XS · **Branch:** `feat/met-417-compound-filters`

**Background.** Today `filters={}` accepts arbitrary keys with unclear
behavior. KB-SRC-014 in `kb-test-plan.md` asks us to pin the contract.

**Files:**
- `tool_registry/tools/knowledge/adapter.py`
- `digital_twin/knowledge/lightrag_service.py` — search method
- `docs/architecture/knowledge-ingestion-playbook.md` — append rule
- `tests/unit/test_knowledge_filters.py` (new)

**Spec:**
1. Pin: filters are AND across keys, equality match. Unknown keys are
   passed through (matches metadata fields literally).
2. Document the rule in the playbook.
3. Reject filter values that are not str / int / bool / None with
   MET-385 envelope.

**Tests:**
- AND semantics: filters `{a: "x", b: "y"}` only return hits where
  both match.
- Unknown key: filters `{banana: "yellow"}` returns 0 hits (no
  metadata key matches), no error.

**Done when:**
- Unit test PASS.
- Playbook updated.
- PR title: `feat(mcp): pin compound-filter AND semantics (MET-417)`.

---

### L1-C1 — `forge sources list/show/delete`

**MET:** MET-411 · **Effort:** S · **Deps:** L1-A8, L1-B1 · **Branch:** `feat/met-411-forge-sources-cli`

**Files:**
- `cli/forge_cli/sources.py` (new)
- `cli/forge_cli/main.py` (register subparser)
- `cli/forge_cli/client.py` (add REST methods if not present)
- `tests/unit/test_forge_sources_cli.py` (new)

**Spec:**
1. `forge sources list [--type <kt>] [--project <uuid>] [--limit N]` — table output
2. `forge sources show <source_path|id>` — full detail incl. chunks
3. `forge sources delete <source_path|id>` — calls `delete_by_source`,
   confirms first unless `--yes`

**Tests:**
- `tests/unit/test_forge_sources_cli.py` with mocked `ForgeClient`.

**Done when:**
- Unit test PASS.
- PR title: `feat(cli): forge sources list/show/delete (MET-411)`.

---

### L1-C2 — CLI error reporting on bad input

**MET:** MET-411 · **Effort:** XS · **Branch:** `fix/met-411-forge-ingest-errors`

**Files:**
- `cli/forge_cli/ingest.py`
- `tests/unit/test_forge_ingest_errors.py` (new)

**Spec:**
1. `forge ingest /nonexistent` → exit code 2, stderr names the path,
   no stack trace.
2. Empty file → per-file warning, run continues, exit 0 if other
   files OK.
3. Binary / unsupported extension in directory walk → per-file warn,
   skip, run continues.

**Tests:**
- Run CLI under `subprocess.run` with each case, capture stderr +
  exit code.

**Done when:**
- Unit test PASS.
- KB-CLI-005, KB-CLI-006 in `kb-test-plan.md` flip to ✅.
- PR title: `fix(cli): actionable errors on bad ingest input (MET-411)`.

---

### L1-C3 — CLI PDF walker integration

**MET:** MET-399 · **Effort:** XS · **Deps:** L1-A3 · **Branch:** `feat/met-399-cli-pdf-walker`

**Files:**
- `cli/forge_cli/ingest.py` — already accepts .pdf in
  `SUPPORTED_EXTENSIONS`; this item only adds an end-to-end test
- `tests/integration/test_forge_ingest_pdf.py` (new)

**Spec:**
1. Run `forge ingest tests/fixtures/datasheets/` (the dir we just built).
2. Assert each PDF is dispatched and returns `chunks_indexed > 0`.

**Tests:**
- Integration test against a small fixture PDF (≤ 200 KB).

**Done when:**
- Integration test PASS.
- HP-INGEST-03 in `tier1/ingest.md` BLOCKED → executable.
- PR title: `test(cli): end-to-end PDF directory ingest (MET-399)`.

---

### L1-D1 — Phase-1 UI shared-workspace alignment

**Status:** ⏸️ **Requires human decision: true.** Pin direction
(Ollama-on-both, Gemini-on-both, or accept separation) before
proceeding. See conversation log + `docs/integrations/lightrag-ui.md`.

---

### L1-D2 — MET-346 adoption checklist green

**MET:** MET-346 · **Effort:** S · **Deps:** L1-D1 · **Branch:** `chore/met-346-adoption-checklist`

**Files:**
- `docs/uat/cycle3-knowledge-ssot-test-run-2026-04-30.md` (extend)
- `docs/integrations/lightrag-ui.md` (update checklist state)

**Spec:**
1. With shared-workspace decision applied, work through the 5-item
   checklist in `docs/integrations/lightrag-ui.md`:
   - Ingest works
   - Graph extraction works
   - Vector retrieval works
   - Hybrid retrieval works (depends on L1-A2)
   - Citation chain complete
2. Document each PASS with evidence in a new run report.

**Tests:** none (acceptance — human runs through the checklist).

**Done when:**
- All 5 boxes ticked in the doc.
- Run report committed.
- PR title: `chore(uat): MET-346 adoption checklist green`.

---

### L1-D3 — Phase-1 docs final pass

**MET:** MET-346 · **Effort:** XS · **Deps:** L1-D2 · **Branch:** `docs/met-346-phase-1-final`

**Files:**
- `docs/integrations/lightrag-ui.md`

**Spec:**
1. Promote the doc from "Phase 1 (engineer dogfood)" to "Phase 1 (adopted)".
2. Add a "Migration to Phase 2" section pointing at L1-E1..E5.

**Done when:** doc updated; PR title: `docs(integrations): Phase-1 LightRAG UI adopted (MET-346)`.

---

### L1-E1 — ADR-010 Phase-2 spec finalized

**Status:** ⏸️ **Requires human decision: true.** ADR-010 currently
outlines the Phase-1/Phase-2 split but does not pin the Phase-2
component scope (sources table, drill-in, ingest tail, embedded graph,
search bar). Owner needs to decide which of these ship in v1.

---

### L1-E2 — `/knowledge` page — sources table

**MET:** new (file under MET-409) · **Effort:** M · **Deps:** L1-B1, L1-E1 · **Branch:** `feat/knowledge-page-sources-table`

**Files:**
- `dashboard/src/pages/KnowledgePage.tsx` (new)
- `dashboard/src/api/endpoints/knowledge.ts` (new)
- `dashboard/src/types/knowledge.ts` (new)
- `dashboard/src/components/layout/Sidebar.tsx` — add nav item
- `dashboard/src/App.tsx` — add route `/knowledge`
- `dashboard/src/__tests__/KnowledgePage.test.tsx` (new)

**Spec:**
1. New page at `/knowledge` route.
2. Fetches `GET /api/v1/knowledge/sources` (REST surface — adapter for
   `KnowledgeService.list_sources` from L1-A8).
3. Renders a sortable table: source_path, knowledge_type chip,
   fragment_count, indexed_at (relative time), metadata.vendor,
   metadata.mpn.
4. Filter chips at the top: knowledge_type, project (if multi-project context).
5. Empty state: "No sources ingested yet — run `forge ingest <path>`".
6. Row click → navigate to `/knowledge/sources/{id}` (drill-in is
   L1-E4 territory; for E2 just stub the route to a placeholder).

**Tests:**
- React Testing Library: render with mocked endpoint, assert rows
  render, sort by indexed_at works, filter chip filters.
- Visual: take a Playwright screenshot for the PR.

**Done when:**
- Unit + RTL tests PASS.
- Playwright screenshot attached to PR.
- PR title: `feat(dashboard): /knowledge sources table page`.

---

### L1-E3 — `/knowledge` page — search bar

**MET:** new · **Effort:** M · **Deps:** L1-E2 · **Branch:** `feat/knowledge-page-search-bar`

**Files:**
- `dashboard/src/pages/KnowledgePage.tsx` (extend)
- `dashboard/src/components/knowledge/SearchPanel.tsx` (new)
- `dashboard/src/api/endpoints/knowledge.ts` (extend with `search`)
- `dashboard/src/__tests__/KnowledgePage.search.test.tsx` (new)

**Spec:**
1. Search input + top_k selector at the top of the page.
2. Calls `GET /api/v1/knowledge/search` and renders hits below.
3. Each hit shows: source_path (link), heading (chunk_index),
   similarity, snippet with the matched query terms highlighted.
4. Click on a hit → opens a side panel with the full chunk content
   and a "Open source" button.

**Tests:**
- RTL: type a query, mock returns 3 hits, assert highlight + click
  opens the side panel.
- Playwright screenshot.

**Done when:**
- Tests PASS.
- PR title: `feat(dashboard): /knowledge search panel`.

---

### L1-E4 — `/knowledge` page — graph embed (Sigma.js)

**MET:** new · **Effort:** L · **Deps:** L1-E2, L1-B1 · **Multi-iteration: yes**.

The loop **may** split this into ≥ 2 iterations. Recommended split:
- **L1-E4a** — backend: register `metaforge://knowledge/graph` resource
  returning `{nodes: [...], edges: [...]}` derived from LightRAG's
  internal graph store.
- **L1-E4b** — frontend: Sigma.js component, click-to-expand, mini-map.

The loop creates `L1-E4a` and `L1-E4b` rows in the status board and
treats this row as ✅ once both children are ✅.

---

### L1-E5 — Sidebar relabel

**MET:** new · **Effort:** XS · **Deps:** L1-E2 · **Branch:** `feat/dashboard-sidebar-knowledge-relabel`

**Files:**
- `dashboard/src/components/layout/Sidebar.tsx`
- `dashboard/src/pages/FilesPage.tsx` (route from /files; rename to "Files" with folder icon)
- `dashboard/src/App.tsx`

**Spec:**
1. Existing nav item `Knowledge → /files` becomes `Files → /files`
   (icon: `folder`).
2. New nav item `Knowledge → /knowledge` (icon: `psychology`).
3. Update any hard-coded "Knowledge" labels referring to /files.

**Done when:**
- Visual: Playwright screenshot showing both items.
- PR title: `chore(dashboard): split Files vs Knowledge in sidebar`.

---

### L1-F1 — Author 26 NEW kb-test-plan rows

**MET:** MET-410 · **Effort:** L · **Multi-iteration: yes** · **Branch prefix:** `test/met-410-`

The loop splits this into ≥ 5 sub-iterations along the file targets in
`kb-test-plan.md` "Authoring queue":

| Sub | Target file | Rows | Branch suffix |
|---|---|---|---|
| F1a | `tier1/full-capability.md` (new) | 7 | `full-capability` |
| F1b | `tier1/cli-error-paths.md` (new) | 2 | `cli-errors` |
| F1c | `tier2/error-envelope-probe.md` (extend) | 7 | `error-envelope` |
| F1d | `tier2/streaming-progress-probe.md` (new) | 3 | `streaming-progress` |
| F1e | `tier2/observability-knowledge-probe.md` (new) | 3 | `observability-knowledge` |
| F1f | `tier1/event-ingest.md` (new) | 3 | `event-ingest` |
| F1g | `tier1/retrieval.md` (extend) | 2 | `retrieval-extend` |

Each sub creates `## Scenario:` blocks per the kb-test-plan §1–§10 IDs
and updates the master plan's Verdict column.

**Done when (per sub):**
- Scenarios parse via the validator agent contract.
- Master plan §-table updated.
- PR title: `test(uat): <sub label> scenarios (MET-410)`.

---

### L1-F2 — Real-content corpus expansion

**MET:** MET-340 · **Effort:** M · **Branch:** `test/met-340-corpus-expansion`

**Files:**
- `tests/fixtures/datasheets/manifest.yaml`
- `tests/fixtures/datasheets/<mpn>.txt` × 5
- `tests/fixtures/datasheets/<mpn>.gt.yaml` × 5
- `tests/uat/scenarios/tier1/datasheets-real.md` (regenerate via script)
- `docs/uat/kb-test-plan.md` §11 (extend catalog)

**Spec:** add 5 datasheets to the corpus. Suggested set:
- STM32H723VGT6 (ST) — flagship MCU with errata
- ESP32-WROOM-32 (Espressif) — wireless module
- nRF52840 (Nordic) — BLE SoC
- LM2596 (TI) — classic buck regulator
- MCP2515 (Microchip) — CAN controller (AEC-Q100 questions)

**Done when:**
- All 5 fixtures fetched, sha256-pinned, gt.yaml authored
  (each substring verified literal in the .txt).
- `python scripts/datasheets/generate_scenarios.py` regenerates
  `tier1/datasheets-real.md` with 5 × 10 = 50 new scenarios.
- Master plan §11 expanded.
- PR title: `test(uat): expand datasheet corpus to 8 parts (MET-340)`.

---

### L1-F3 — Dual-project isolation integration test

**MET:** MET-401 · **Effort:** S · **Deps:** L1-A1 · **Branch:** `test/met-401-project-isolation`

**Files:**
- `tests/integration/test_knowledge_project_isolation.py`

**Spec:** standalone integration test (separate from the unit-level
test in L1-A1) that runs against a real pgvector instance with two
real project UUIDs.

**Done when:** test PASS in CI; PR title:
`test(integration): dual-project knowledge isolation (MET-401)`.

---

### L1-F4 — Citation round-trip integration test

**MET:** MET-389 · **Effort:** S · **Branch:** `test/met-389-citation-roundtrip`

**Files:**
- `tests/integration/test_knowledge_citation_roundtrip.py`

**Spec:** ingest a multi-paragraph markdown with H2 headings; search
for a phrase from paragraph 2; assert the returned hit's
`source_path`, `heading`, `chunk_index` round-trip exactly the values
expected from the source file.

**Done when:** test PASS; PR title:
`test(integration): citation round-trip (MET-389)`.

---

### L1-F5 — Neo4j ↔ in-memory parity test

**MET:** new · **Effort:** S · **Branch:** `test/knowledge-graph-parity`

**Files:**
- `tests/unit/test_knowledge_graph_parity.py`

**Spec:** parametrize over `InMemoryGraphEngine` and `Neo4jGraphEngine`
backends; ingest the same fixtures; assert query results identical
(modulo ordering ties).

**Done when:** parametrized test PASS for both backends; PR title:
`test(unit): graph-engine parity for KB queries`.

---

## Final report contract

When the loop stops (any reason), it appends a section to this file:

```markdown
## Run history

### Run YYYY-MM-DDTHH:MM:SSZ
- Stop reason: <all-done | stalled | time-boxed>
- Items processed: N
- ✅ <list of ids>
- ⏸️ <list of ids and reasons>
- ❌ <list of ids and reasons>
- PRs opened: <links>
```

The user reads this section to decide what to merge / re-trigger.
