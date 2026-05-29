# MCP e2e suite — phase state

## Phase 1 — Harness skeleton ✅ DONE (this PR)

Files landed:

- `__init__.py` — module docstring + run-mode overview
- `_helpers.py` — `rpc()`, `call_tool()`, `parse_tool_result()`, `McpRpcError`
- `conftest.py` — session-scoped `mcp_live_url` + `mcp_client` fixture
  - In-process mode (default): builds the FastAPI app via `build_http_app` against in-memory adapter backends. Drives via `httpx.ASGITransport`.
  - Live mode (opt-in): `METAFORGE_MCP_URL=http://fidel-dev:8765` points the same client at the deployed server.
- `test_handshake.py` — `initialize`, `tools/list` non-empty, entries have required fields
- `test_tool_inventory.py` — min-count floor, unique ids, dot-namespaced ids, every inputSchema is `type: object`

## Phase 2 — Gap fixes

### G1 — MCP server wires insight_store ✅ DONE + LIVE-VERIFIED (PR #267)
- `build_unified_server`, `_build_insight_store`, `_close_insight_store` shipped.
- Live probe on fidel-dev confirms: `memory.list_insights` no longer raises `set_insight_store was never called`. Error message **shifted to** "another operation is in progress" — that's G3 (pool contention), not G1. G1 is genuinely fixed.
- Test in `test_memory_tools.py` is the regression guard.
- Process-cleanup gotcha: the live MCP server had a stale process holding the port through prior fires. `docker compose restart gateway` + relaunch picked up the new code cleanly.

### G2 — CadQuery in MCP bootstrap ✅ DONE + LIVE-VERIFIED (PR #269)
Root cause: `METAFORGE_ADAPTER_CADQUERY_URL=http://cadquery-adapter:8101` in the gateway env told bootstrap to fetch the adapter remotely; that container doesn't exist in single-container setups, the HTTP fetch failed, cadquery landed in `failed` and never fell through to in-process.

Fix: on remote-fetch failure, log a warning and drop through to `_create_adapter`. Production deploys with the remote container still get the remote path; dev / single-container deploys get the in-process fallback.

**Live verification on fidel-dev** (post-merge):
- Tool count: **28 → 35** (the 7 cadquery tools landed)
- `tools/list` now includes `cadquery.boolean_operation`, `cadquery.create_assembly`, `cadquery.create_parametric`, `cadquery.execute_script`, `cadquery.export_geometry`, `cadquery.generate_enclosure`, `cadquery.get_properties`

### G3 — MCP event-loop unification ✅ DONE + LIVE-VERIFIED (PR #271)
Root cause was NOT pool sharing across processes — it was loop binding within the same process. `metaforge.mcp.__main__:main()` did:
```
server, ... = asyncio.run(_bootstrap(args))    # creates pools on loop A, destroys loop A
run_http(server, ...)                          # uvicorn creates loop B, queries fail
asyncio.run(_close_*)                          # would also try yet another loop
```
asyncpg pools are bound to the event loop they were created in. By the time uvicorn served the first request, loop A was dead and every `pg_store.list()` / `pg_store.search()` failed with `"another operation is in progress"`.

Fix: introduce `serve_http_async()` (a coroutine that awaits `uvicorn.Server.serve()`) and run bootstrap + uvicorn + cleanup inside one `asyncio.run(_http_main())`. `run_http` stays as a sync wrapper for back-compat (delegates to `serve_http_async`).

Tests (`tests/unit/test_mcp_serve_http_loop.py`):
- `serve_http_async` is a coroutine function (regression guard)
- Stub uvicorn.Server: verify `.serve()` is called, `.run()` is NOT
- `run_http` delegates via exactly one `asyncio.run`

**Live verification on fidel-dev** (post-merge):
```
memory.list_insights:                OK status=success count=0
memory.retrieve_similar_experience:  OK status=success count=3
```
Both memory tools now return clean envelopes. `retrieve_similar_experience` even surfaces the 3 existing `agent_experiences` rows from the earlier Mechanical-agent recording.

### G4 — extract_properties LLM-over-chunks fallback ✅ DONE (PR #273, merged 2026-05-29)

Root cause: every text-ingested datasheet lands in pgvector chunks but no
Twin ``Datasheet`` node is created (the ingest pipeline tied to MET-444's
structured-table extraction only fires on PDF ingest). So
``knowledge.extract`` returned ``mpn_found=False`` for everything in the
populated KB even though the prose was sitting right there.

Fix: ``extract_properties_for_mpn`` now accepts an optional ``search``
callable. When ``twin.get_current_datasheet(mpn)`` returns None AND both
``search`` and ``llm`` are wired, we pull the top ``fallback_top_k`` (=5)
hits, concat their content, and run the existing Tier-2/3
``infer_property`` per property against that synthesised prose.
``LightRAGKnowledgeService.extract_properties`` binds a closure around
``self.search`` and forwards it. Fallback path is gated on the LLM being
configured — same gate the existing Tier-2/3 path uses.

Contract additions:
- ``mpn_found=True`` even though no ``Datasheet`` node exists.
- ``datasheet_revision=None`` (sentinel for "synthesised from chunks").
- ``datasheet_source_path`` is the source path of the top-ranked hit.
- Search backend failures degrade to ``mpn_found=False`` rather than
  crashing the call.

Tests (`tests/unit/test_knowledge_property_extractor_llm.py`):
- happy path (one-property fallback returns ``llm_inferred``)
- no-search disables fallback (pre-G4 contract preserved)
- no-llm disables fallback (no point calling search)
- empty hits → ``mpn_found=False``
- broken search backend → ``mpn_found=False`` (fail-open)
- multi-property: one search round-trip fuels N per-property LLM calls

**Live verification: DEFERRED.** The MCP HTTP server on fidel-dev wasn't
running at G4 merge time (the host process started for the G3 verify had
exited; the gateway container itself doesn't run the MCP HTTP server).
G4's behaviour also depends on a property LLM being wired, which needs
``OPENROUTER_API_KEY`` / ``METAFORGE_PROPERTY_LLM_PROVIDER`` in the MCP
process environment. Live re-launch + verify is a follow-up — captured
in the suite as the `knowledge.extract` happy-path scenario landing in
Phase 3.

## Phase 3 — Per-tool happy-path coverage (next)

Next file: `test_knowledge_tools.py`. Covers `knowledge.search`,
`knowledge.ingest`, `knowledge.extract` (G4 unblocker), and
`knowledge.populate_bom`. One canonical happy-path call per tool with
real KB data; in-process mode against the InMemory adapters by default,
live mode pinned by `METAFORGE_MCP_URL` for the deferred G4 verify.

## Phase 4-7 — pending
