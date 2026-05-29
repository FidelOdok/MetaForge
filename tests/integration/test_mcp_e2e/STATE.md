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

## Phase 3 — Per-tool happy-path coverage

### `test_knowledge_tools.py` ✅ DONE (this PR)

Covers all four `knowledge.*` MCP tools end-to-end through the JSON-RPC
HTTP layer:

- `knowledge.search` — citation round-trip + `top_k` forwarding + `knowledge_type` enum validation
- `knowledge.ingest` — single-payload mode, asserts `chunks_indexed` + enum forwarding
- `knowledge.extract` — `ExtractedProperties` envelope shape, input-order preservation, missing/empty-args error path
- `knowledge.populate_bom` — search → extract → rank smoke; deduped candidate by MPN
- `tools/list` inventory check confirms all four tools register when `knowledge_service` is wired

In-process mode wires a `_FakeKnowledgeService` (KnowledgeService Protocol)
into `build_unified_server` via a module-level `knowledge_mcp_client`
fixture. Live mode (`METAFORGE_MCP_URL`) routes the same fixture at the
deployed MCP server — that's where the deferred G4 live-verify will run.

Tool counts after this PR: 14 e2e tests pre-existing + 7 new = 21 in
`tests/integration/test_mcp_e2e/`.

### `test_memory_tools.py` ✅ DONE (extended in this PR)

Existing G1 smoke (3 tests against the shared session `mcp_client`)
expanded with 6 more cases using a module-local `memory_mcp_client`
fixture that wires fresh `InMemoryExperienceStore` +
`InMemoryInsightStore`:

- `memory.list_insights` round-trips an active insight with the right
  theme / status / confidence shape
- `memory.list_insights` filters STALE_WARN by default (MET-472
  lifecycle); `include_stale=true` returns both
- `memory.list_insights` rejects an unknown theme with `McpRpcError`
- `memory.retrieve_similar_experience` finds a pre-indexed match
  (embed → store → MCP retrieve) and returns agent_code / task_type /
  success / similarity
- `memory.retrieve_similar_experience` rejects missing + empty `goal`
- `tools/list` inventory confirms both memory tools register

Tool counts after this PR: 21 → 27 e2e tests.

### `test_twin_tools.py` ✅ DONE (this PR)

All five `twin.*` MCP tools covered with a module-local `twin_mcp_client`
+ `twin_with_data` fixture that wires a fresh `InMemoryTwinAPI`
pre-populated with one canonical `WorkProduct`.

- `twin.get_node` — root + first-hop envelope; invalid UUID + missing
  arg yield clean `McpRpcError`
- `twin.thread_for` — depth-bounded subgraph round-trip; depth-range
  validation (1..10) rejects out-of-bounds
- `twin.find_by_property` — adapter validation: unsafe Cypher labels +
  missing value rejected at the adapter, before forwarding (no
  injection path). The Cypher happy-path requires the Neo4j twin and
  is gated on `METAFORGE_MCP_URL` (skipped in CI in-process mode).
- `twin.constraint_violations` — empty branch passes, evaluated_count
  shape verified
- `twin.query_cypher` — read-only enforced (CREATE / MERGE / DELETE
  rejected by the adapter), empty cypher rejected. Cypher-backed read
  happy-path is `_REQUIRES_LIVE_TWIN`-gated.
- `tools/list` inventory confirms all five tools register.

Tool counts after this PR: 27 → 38 e2e tests + 2 live-only skips.

### `test_project_tools.py` ✅ DONE (this PR)

All three `project.*` MCP tools driven against
`api_gateway.projects.backend.InMemoryProjectBackend` (already satisfies
the adapter's `ProjectBackendLike` protocol).

- `project.create` persists and returns id + timestamps; missing /
  empty `name` rejected
- `project.list` empty + 2-record cases
- `project.get` by id round-trip, by name, unknown id returns null
  payload, missing both args raises `McpRpcError`
- `tools/list` inventory confirms all three register

Tool counts after this PR: 38 → 47 e2e tests + 2 live-only skips.

### `test_constraint_tools.py` ✅ DONE (this PR)

Single `constraint.validate` tool, driven against
`InMemoryConstraintEngine` wired to a twin with one WorkProduct + one
trivially-passing constraint.

- Empty `work_product_ids` → vacuous pass, evaluated_count=0
- WP with attached constraint → evaluated_count=1, passed=True
- Unknown UUID → resolves to zero constraints, vacuous pass
- Missing `work_product_ids` → `McpRpcError`
- Non-UUID strings → `McpRpcError`
- Non-list input → `McpRpcError`
- `tools/list` inventory confirms `constraint.validate` registers

Tool counts after this PR: 47 → 54 e2e tests + 2 live-only skips.

### `test_cad_tools.py` ✅ DONE (this PR — extended)

Existing G2 inventory smokes joined by full per-adapter coverage:

- Inventory: all 7 cadquery + 5 freecad + 4 calculix tools register
  (G2 regression guard tightened to a fixed set per adapter)
- Total CAD/sim tool count == 16
- KiCad **explicitly absent** from the unified MCP bootstrap — KiCad
  ships as a separate stdio entrypoint and isn't in
  `tool_registry.bootstrap._ADAPTER_REGISTRY`. The test is the gap
  tripwire; flipping it means the EE vertical scenarios should
  exercise KiCad too.
- Adapter-level validation: missing required args + invalid enums
  rejected by `cadquery.create_parametric` / `cadquery.boolean_operation` /
  `cadquery.get_properties` / `freecad.create_parametric` /
  `freecad.export_geometry` / `calculix.run_fea` / `calculix.validate_mesh`
  before forwarding to the backend (which is what makes them
  runnable in CI without the CAD libs installed)

Full happy-path execution (real cadquery, FreeCAD headless, ccx solver)
lives in Phase 5 live-mode vertical scenarios.

Tool counts after this PR: 54 → 68 e2e tests + 2 live-only skips.

### `test_supplier_tools.py` ✅ DONE (this PR)

Distributor coverage with a CI-friendly fake-adapter path plus
live-cred gated smokes:

- Default mode (no creds): all three distributors (digikey / mouser /
  nexar) are absent from `tools/list` — MET-434 "skip with structured
  log" contract enforced
- Fake-adapter mode: a `_FakeDistributorAdapter` patched into
  `tool_registry.tools.digikey.adapter.DigiKeyAdapter` so bootstrap's
  `_make_digikey()` returns the stub with fake env creds. Tests then
  drive `digikey.search`, `digikey.get_product`, `digikey.get_pricing`,
  `digikey.get_availability` against the fake — quantity-tier
  sort, null returns on unknown MPN, missing-arg → `McpRpcError`,
  inventory check
- Live mode (`DIGIKEY_CLIENT_ID/SECRET`, `MOUSER_API_KEY`,
  `NEXAR_CLIENT_ID/SECRET`): smoke tests against the real APIs gated
  on the relevant credentials; skip in CI

Tool counts after this PR: 68 → 76 e2e tests + 5 live-only skips
(2 Cypher + 3 distributor credential gates).

## Phase 3 — COMPLETE

All seven Phase 3 per-tool files are merged. The MCP surface is
covered end-to-end through the JSON-RPC HTTP envelope:

| File                     | PR    | Tools covered                                    |
|--------------------------|-------|--------------------------------------------------|
| test_knowledge_tools.py  | #275  | search / ingest / extract / populate_bom         |
| test_memory_tools.py     | #276  | retrieve_similar_experience / list_insights      |
| test_twin_tools.py       | #277  | get_node / thread_for / find_by_property / +2    |
| test_project_tools.py    | #278  | create / list / get                              |
| test_constraint_tools.py | #279  | validate                                         |
| test_cad_tools.py        | #280  | cadquery + freecad + calculix inventory + valid. |
| test_supplier_tools.py   | this  | digikey + mouser + nexar (fake + live-gated)     |

## Phase 4 — Error-path coverage ✅ DONE (this PR)

`test_mcp_errors.py` covers the JSON-RPC error envelope contract for
every documented failure mode. Error codes (`metaforge/mcp/server.py`):

- `-32600` INVALID_REQUEST — malformed JSON, missing/wrong `jsonrpc`
- `-32601` METHOD_NOT_FOUND — unknown method, unknown tool name (data
  carries `tool_id`)
- `-32001` TOOL_EXECUTION_ERROR — handler raised (data carries
  `error_type / tool_id / details / duration_ms`)

Cases:
- Invalid JSON body → -32600
- Wrong `jsonrpc` version → -32600
- Unknown RPC method → -32601
- Unknown tool name → -32601 with `data.tool_id`
- Missing required arg (`twin.get_node` without `node_id`) → -32001
  with `data.tool_id` + `data.details`
- Invalid UUID → -32001 with "uuid" in details
- Invalid enum value (`cadquery.create_parametric` unknown shape) →
  -32001
- Mutating Cypher rejected when read-only → -32001 with "mutating"
  or "read-only" in details
- Error envelope shape: `jsonrpc==2.0`, id round-trip, no `result`,
  `code: int < 0`, `message: str`
- `initialize` with empty params succeeds (spec compliance)
- `tools/call` without `arguments` key → defaults to `{}`, then
  the handler errors with -32001 (proves dispatcher doesn't crash)

**MET-450 64 KiB stdio readline guard**: skipped — the guard is the
default `asyncio.StreamReader` limit on `__main__.run_stdio()`; the
HTTP transport has no body cap of its own. The test is a documented
`@pytest.mark.skip` placeholder; the real stdio guard test belongs
in a subprocess-driven runner that spawns the MCP CLI and pipes a
>64 KiB line through stdin.

Tool counts after this PR: 76 → 87 e2e tests + 6 live/skip
(2 Cypher + 3 distributor cred + 1 stdio guard).

## Phase 5-7 — pending
