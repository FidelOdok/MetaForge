# Cycle 1 + 2 Acceptance Matrix

> **Status:** P1.15 UAT validation. Every acceptance bullet from every
> Cycle 1 or Cycle 2 Linear issue is mapped here to a Track-A pytest
> test or a Track-B Claude-driven scenario. A bullet without a test
> path is a gap — file it as a Cycle-3 follow-up.

This matrix is the durable artefact behind the UAT runs. The reports
in `docs/uat/uat-report-*.md` and `docs/uat/uat-claude-driven-report-*.md`
are point-in-time snapshots; this file is the standing contract.

## How to read this

| Column | Meaning |
|---|---|
| **MET** | Linear issue id |
| **Acceptance bullet** | The ✓ / ✗ checkbox text from the issue body |
| **Track** | A = pytest wire-level · B = Claude-driven scenario |
| **Test / scenario** | File path (Track A) or scenario heading (Track B) |
| **Notes** | Where the test diverges from the literal bullet, why |

A bullet covered by an existing non-UAT test (e.g. an integration
test from earlier work) is annotated `(existing: <path>)` and
re-asserted briefly in the matching UAT layer for traceability.

---

## Cycle 1 — Context Engineering (epic MET-312)

### Layer 0 · Persistence

| MET | Acceptance bullet | Track | Test / scenario |
|---|---|---|---|
| 292 | Data survives connection close + reopen | A | `cycle1/test_l0_persistence.py::test_met305_data_survives_round_trip` |
| 292 | Twin backend is real Neo4j when configured | A | `cycle1/test_l0_persistence.py::test_met292_twin_backend_is_real_neo4j_when_configured` |
| 304 | NEO4J_URI scheme is bolt:// + port reachable | A | `cycle1/test_l0_persistence.py::test_met304_neo4j_uri_reachable` |
| 305 | PostgreSQL connection succeeds | A | `cycle1/test_l0_persistence.py::test_met305_postgres_connection_succeeds` |
| 305 | pgvector extension installed | A | `cycle1/test_l0_persistence.py::test_met305_pgvector_extension_loaded` |

### Layer 1 · Knowledge retrieval

| MET | Acceptance bullet | Track | Test / scenario |
|---|---|---|---|
| 346 | KnowledgeService Protocol — ingest returns chunks_indexed | A | `cycle1/test_l1_retrieval.py::test_met346_ingest_and_search_round_trip` |
| 293 | Search returns ranked hits for the ingested phrase | A | `cycle1/test_l1_retrieval.py::test_met346_ingest_and_search_round_trip` |
| 293 | Search latency under sub-second | A | `cycle1/test_l1_retrieval.py::test_met293_search_latency_under_one_second` |
| 307 | Consumer depends on Protocol, not concrete impl | A | `cycle1/test_l1_retrieval.py::test_met307_consumer_imports_protocol_not_concrete` |
| 307 | Update event drops stale chunks (existing: `tests/integration/test_knowledge_event_flow.py`) | B (Tier 2) | `tests/uat/scenarios/tier2/dedup-probe.md` |
| 335 | knowledge.* MCP adapter exposes search + ingest | A | `cycle1/test_l1_retrieval.py::test_met335_knowledge_adapter_registers_two_tools` |
| 336 | `forge ingest <dir>` walks markdown files | A | `cycle1/test_l1_retrieval.py::test_met336_forge_ingest_walks_markdown` |
| 346 | knowledge.search returns just-ingested doc (Claude POV) | B (Tier 1) | `scenarios/tier1/knowledge.md::scenario "ingest then search"` |

### Layer 2 · Context assembly

| MET | Acceptance bullet | Track | Test / scenario |
|---|---|---|---|
| 313 | Context Engineering spec doc exists | A | `cycle1/test_l2_context_assembly.py::test_met313_context_engineering_spec_exists` |
| 315 | Assembler returns at least one fragment for a query | A | `cycle1/test_l2_context_assembly.py::test_met315_assemble_returns_attributed_fragments` |
| 316 | Mechanical agent does not receive COMPONENT knowledge | A | `cycle1/test_l2_context_assembly.py::test_met316_role_scope_narrows_to_allowed_types` |
| 317 | Token budget enforced; dropped reported | A | `cycle1/test_l2_context_assembly.py::test_met317_token_budget_drops_lowest_priority` |
| 317 | response.truncated reflects drop state | A | (same test) |
| 319 | Mechanical Agent context spec doc exists | A | `cycle1/test_l2_context_assembly.py::test_met319_332_per_agent_context_spec_docs_exist[mechanical]` |
| 320 | Every fragment carries source_id + source_kind | A | `cycle1/test_l2_context_assembly.py::test_met315_assemble_returns_attributed_fragments` |
| 332 | Electronics Agent context spec doc exists | A | `cycle1/test_l2_context_assembly.py::test_met319_332_per_agent_context_spec_docs_exist[electronics]` |

### Layer 3 · Quality signals

| MET | Acceptance bullet | Track | Test / scenario |
|---|---|---|---|
| 322 | Voltage disagreement surfaces on shared MPN | A | `cycle1/test_l3_quality_signals.py::test_met322_conflict_detector_emits_warning_on_voltage_disagreement` |
| 322 | Severity ladder reaches WARNING+ for value-bearing fields | A | (same test) |
| 323 | Fresh fragment has staleness < 0.2 | A | `cycle1/test_l3_quality_signals.py::test_met323_compute_staleness_returns_unit_interval` |
| 323 | Year-old fragment has higher staleness than fresh | A | (same test) |
| 323 | Superseded fragment scores 1.0 | A | (same test) |
| 324 | Two fragments sharing MPN collapse to one cluster | A | `cycle1/test_l3_quality_signals.py::test_met324_identity_resolver_clusters_by_mpn` |
| 324 | R12 with two MPNs surfaces an IdentityMismatch | A | `cycle1/test_l3_quality_signals.py::test_met324_identity_resolver_flags_mpn_mismatch_at_same_refdes` |
| 326 | precision/recall/MRR/NDCG return values in [0,1] | A | `cycle1/test_l3_quality_signals.py::test_met326_precision_recall_mrr_ndcg_in_unit_interval` |
| 326 | Evaluator runs against a fake KnowledgeService | A | `cycle1/test_l3_quality_signals.py::test_met326_evaluator_runs_against_fake_service` |
| 333 | MPN mismatch produces has_blocking_conflict=True | A | `cycle1/test_l3_quality_signals.py::test_met333_blocking_conflict_flips_response_flag` |
| 334 | Truncation counter records drops when budget tight | A | `cycle1/test_l3_quality_signals.py::test_met334_truncation_metric_increments_on_drop` |
| 323 | Fresh-vs-stale visible in agent-driven session | B (Tier 2) | `scenarios/tier2/staleness-probe.md` |

### Layer 4 · Extension recipes

| MET | Acceptance bullet | Track | Test / scenario |
|---|---|---|---|
| 314 | Knowledge Ingestion Playbook exists | A | `cycle1/test_l4_extension_recipes.py::test_met314_ingestion_playbook_exists_and_cross_linked` |
| 314 | Cross-linked from context-engineering.md | A | (same test) |
| 329 | Quote capture records and returns chronological history | A | `cycle1/test_l4_extension_recipes.py::test_met329_quote_capture_records_and_returns_history` |
| 329 | Trend % reports positive for rising prices | A | (same test) |
| 329 | Quotes link to BOM items | A | (same test) |
| 331 | Identical params have distance=0 in find_similar | A | `cycle1/test_l4_extension_recipes.py::test_met331_simulation_capture_round_trip_and_similarity` |
| 331 | Solver change increases distance | A | (same test) |
| 331 | Fingerprint deterministic for equivalent params | A | `cycle1/test_l4_extension_recipes.py::test_met331_fingerprint_is_deterministic` |

---

## Cycle 2 — MCP Harness

### Layer 1 · Standalone server + client bridge

| MET | Acceptance bullet | Track | Test / scenario |
|---|---|---|---|
| 337 | Subprocess boots with metaforge-mcp ready signal | A | `cycle2/test_l1_mcp_server.py::test_met337_subprocess_boots_and_lists_seven_tools` |
| 337 | tool/list returns ≥7 tools | A | (same test) |
| 306 | Bridge enforces wait_for timeout | A | `cycle2/test_l1_mcp_server.py::test_met306_bridge_enforces_timeout` |
| 306 | Factory falls back when subprocess fails | A | `cycle2/test_l1_mcp_server.py::test_met306_factory_falls_back_when_command_fails` |
| 306 | METAFORGE_REQUIRE_MCP=true raises on missing config | A | `cycle2/test_l1_mcp_server.py::test_met306_factory_require_flag_raises` |

### Layer 2 · Auth + config

| MET | Acceptance bullet | Track | Test / scenario |
|---|---|---|---|
| 338 | Open mode (no key) accepts every connection | A | `cycle2/test_l2_auth_and_config.py::test_met338_verify_api_key_open_mode_when_expected_unset` |
| 338 | Missing/wrong key rejected | A | `test_met338_verify_api_key_rejects_missing_and_mismatch` |
| 338 | Matching key passes constant-time compare | A | `test_met338_verify_api_key_accepts_match` |
| 338 | redact() preserves last 4 chars | A | `test_met338_redact_keeps_only_last_four_chars` |
| 338 | HTTP rejects missing/wrong Authorization (401) | A | `test_met338_http_rejects_missing_authorization` + `test_met338_http_accepts_correct_bearer` |
| 338 | /health remains open under auth | A | `test_met338_http_health_endpoint_remains_open` |
| 338 | Stdio mismatch emits auth_error JSON-RPC | A | `test_met338_stdio_rejects_mismatch_at_launch` |
| 339 | .mcp.json contains metaforge entry targeting stdio module | A | `test_met339_metaforge_entry_targets_stdio_module` |
| 339 | mcp-config-examples doc exists | A | `test_met339_config_examples_doc_exists` |

### Layer 3 · External-harness E2E

| MET | Acceptance bullet | Track | Test / scenario |
|---|---|---|---|
| 340 | tool/list ≥7 through external client | A | `cycle2/test_l3_external_harness.py::test_met340_external_harness_round_trip` |
| 340 | health/check returns status=healthy + roll-up | A | (same test) |
| 340 | Unknown tool returns -32601 | A | (same test) |
| 340 | Round-trip well under 60s | A | (same test) |
| 340 | Same path runs in CI on every PR | A | gated by `pytest.mark.integration` in CI |

### Layer 4 · Integration docs

| MET | Acceptance bullet | Track | Test / scenario |
|---|---|---|---|
| 341 | claude-code.md exists with prerequisite/`.mcp.json`/`/mcp`/troubleshooting sections | A | `cycle2/test_l4_docs_links.py::test_met341_claude_code_doc_exists_with_expected_sections` |
| 342 | codex.md exists with HTTP examples (Bearer, /health, /mcp) | A | `cycle2/test_l4_docs_links.py::test_met342_codex_doc_exists_with_http_examples` |
| 341+342 | Both docs cross-link mcp-config-examples | A | `test_met341_342_docs_cross_link_examples_doc` |
| 341+342 | All relative markdown links resolve | A | `test_met341_342_relative_links_resolve` (parametrised over 3 docs) |

---

## Track B — Claude-driven scenarios (added in follow-up branch)

The pytest matrix above covers contract correctness. The Track-B
scenarios cover *user experience* from a Claude perspective. Each
tier's scenario file lives at `tests/uat/scenarios/tierN/<group>.md`.

| Tier | Scenario file | What it validates |
|---|---|---|
| 0 | `tier0/golden-flow.md` | Claude → MCP → ingest → search → tool/call works end-to-end (one scenario, every PR) |
| 1 | `tier1/knowledge.md` | 6–10 scenarios across `knowledge.search` / `knowledge.ingest` (Cycle gates) |
| 1 | `tier1/cadquery.md` | 6–10 scenarios across the seven cadquery tools |
| 1 | `tier1/calculix.md` | 6–10 scenarios across the four calculix tools |
| 2 | `tier2/staleness-probe.md` | Claude reads logs/metrics to verify staleness behaviour (weekly) |
| 2 | `tier2/provenance-probe.md` | Source attribution chain visible end-to-end |
| 2 | `tier2/dedup-probe.md` | Update-event dedup observable from outside the gateway |

The scenario files arrive on `feat/uat-claude-driven-validator` after
the wire-level branch merges; their entries here are placeholders
locked into the matrix so the contract stays complete.

## Open gaps (filed in Cycle 3 as they're discovered)

This section is updated by each UAT run. After the first run the
Track-A `uat-report-<date>.md` and Track-B
`uat-claude-driven-report-<date>.md` link any gap tickets back here.
