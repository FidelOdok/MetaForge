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

### G4 — extract_properties LLM-over-chunks fallback (next)

## Phase 3-7 — pending
