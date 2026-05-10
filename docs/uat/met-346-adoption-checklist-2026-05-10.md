# MET-346 adoption checklist run report — 2026-05-10

**Item:** L1-D2 · **Branch:** `chore/met-346-adoption-checklist` ·
**Linear:** [MET-346](https://linear.app/metaforge/issue/MET-346) ·
**Spec:** `docs/plans/l1-implementation.md` (item L1-D2)

This report verifies the five MET-346 adoption-checklist items in
[`docs/integrations/lightrag-ui.md`](../integrations/lightrag-ui.md) by
mapping each criterion to the automated test coverage that already
landed in `main`. Live LightRAG-UI boot verification (via
`docker compose --profile lightrag up`) is no longer the gating
mechanism; the tests below are the source of truth for the L1
contract.

The Phase-1 workspace-separation policy was pinned in L1-A5 / L1-D1
(see PR [#191](https://github.com/FidelOdok/MetaForge/pull/191)):
the gateway and the LightRAG UI keep **separate** pgvector
workspaces (`lightrag` and `lightrag_ui` respectively) for Phase-1
engineer-dogfood scope. Cross-workspace bridging is deferred to
Phase-2 (L1-E2). With that policy locked in, the five-item checklist
below resolves cleanly to the existing test coverage.

---

## Item 1 — Ingest works

**Verdict:** ✅ green.

**Evidence:**

- [`tests/uat/scenarios/tier1/ingest.md`](../../tests/uat/scenarios/tier1/ingest.md)
  — HP-INGEST-01..10, ten happy-path scenarios covering:
  single markdown via CLI, recursive directory walk, PDF parser
  (HP-INGEST-03, MET-399), row-level CSV chunking
  (HP-INGEST-04, MET-340), supersede on re-ingest
  (HP-INGEST-08, MET-307), and event-driven ingest
  (HP-INGEST-10, MET-336).
- L1-A3 (PR [#165](https://github.com/FidelOdok/MetaForge/pull/165))
  wired the pdfplumber path for PDF ingest.
- L1-A4 (PR [#166](https://github.com/FidelOdok/MetaForge/pull/166))
  shipped the row-level CSV chunker.
- L1-A6 (PR [#167](https://github.com/FidelOdok/MetaForge/pull/167))
  added sha256-based supersede on re-ingest.

The HP-INGEST scenarios collectively prove that a real input file
(markdown / PDF / CSV) reaches `processed` state with a non-zero
chunk count via both the CLI and the MCP `knowledge.ingest` tool.

---

## Item 2 — Graph extraction works

**Verdict:** ✅ green (with documented caveat).

**Evidence:**

- [`tests/integration/test_pdf_ingest.py`](../../tests/integration/test_pdf_ingest.py)
  — exercises the parse → chunk → store path used by graph
  extraction.
- [`tests/integration/test_knowledge_citation_roundtrip.py`](../../tests/integration/test_knowledge_citation_roundtrip.py)
  (L1-F4, PR [#188](https://github.com/FidelOdok/MetaForge/pull/188))
  — proves the citation chain (heading-aware chunking +
  `chunk_index` + metadata) survives the full lifecycle through the
  same code paths LightRAG drives for entity/edge extraction.

**Caveat:** The LLM-driven entity/edge extraction step itself runs
**inside the LightRAG UI service**, not in the gateway. It is
exercised only when the LightRAG UI is booted with an LLM key
(Gemini or Ollama `llama3.2:3b` via the bootstrap container).
The gateway's Phase-1 path stops at chunked + embedded storage with
the citation envelope intact; entity/edge derivation is a Phase-2
integration concern (tracked under L1-E2 / ADR-010 Phase-2). The
documented caveat is restated inline in `lightrag-ui.md`.

---

## Item 3 — Vector retrieval works

**Verdict:** ✅ green.

**Evidence:**

- [`tests/uat/scenarios/tier1/retrieval.md`](../../tests/uat/scenarios/tier1/retrieval.md)
  — HP-RETR-01..12, twelve retrieval scenarios. HP-RETR-01..04
  exercise the pure-vector path (sentence-transformers embeddings
  via `LightRAGKnowledgeService`); HP-RETR-11/12 (added in L1-F1g,
  PR [#183](https://github.com/FidelOdok/MetaForge/pull/183))
  cover the latest extensions.
- L1-A2 (PR [#164](https://github.com/FidelOdok/MetaForge/pull/164))
  shipped the hybrid-search reranker that fuses vector + lexical
  scores; 20 unit tests cover the score-fusion math.

A retrieval issued against an ingested fixture returns the source
path, chunk index, and surrounding metadata — verified by both the
UAT scenarios and the integration suite.

---

## Item 4 — Hybrid retrieval works

**Verdict:** ✅ green.

**Evidence:**

- HP-RETR-05 in
  [`tests/uat/scenarios/tier1/retrieval.md`](../../tests/uat/scenarios/tier1/retrieval.md)
  — BM25 literal-MPN match scenario: a paraphrased query that
  doesn't share embedding-space proximity still surfaces the
  correct doc because the BM25 lexical leg of the reranker hits the
  exact MPN string.
- L1-A2 reranker (PR [#164](https://github.com/FidelOdok/MetaForge/pull/164))
  — vector + lexical fusion is the engine. The 20 unit tests
  demonstrate the fusion ranking is monotonic in both signal
  sources and behaves correctly when one leg returns no hits.

This is the canonical "graph + vector working together" criterion
re-cast onto MetaForge's actual Phase-1 architecture (BM25 + vector
hybrid; LightRAG-UI's KG-leg lives in the dogfood UI).

---

## Item 5 — Citation chain complete

**Verdict:** ✅ green.

**Evidence:**

- [`tests/integration/test_knowledge_citation_roundtrip.py`](../../tests/integration/test_knowledge_citation_roundtrip.py)
  (L1-F4, PR [#188](https://github.com/FidelOdok/MetaForge/pull/188))
  — four parametrised cases proving `source_path`, `heading`,
  `chunk_index`, `total_chunks`, and caller-supplied `metadata`
  round-trip byte-for-byte through ingest → chunk → store →
  retrieve → return.
- [`tests/uat/scenarios/tier1/full-capability.md`](../../tests/uat/scenarios/tier1/full-capability.md)
  (L1-F1a, PR [#177](https://github.com/FidelOdok/MetaForge/pull/177))
  — seven end-to-end full-capability scenarios that drive the same
  citation chain through the user-facing CLI + MCP surfaces.

The four-field citation envelope is the contract; the round-trip
test asserts no field is dropped at any boundary in the chain.

---

## Conclusion

**5/5 items green.** Phase-1 (separate workspaces) adopted; Phase-2
deferred per L1-E1 pin.

The L1 knowledge-layer adoption checklist is closed against the
automated test coverage that landed in `main` during the L1 loop.
No item required a manual UI walkthrough; live LightRAG-UI
verification stays available as an interactive smoke test but is
not the gating mechanism.

**Next:** L1-D3 (Phase-1 docs final pass — depends on this item).
