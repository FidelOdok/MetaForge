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

### G2 — CadQuery in MCP bootstrap (next)
Live tools/list still shows zero `cadquery.*` tools despite the in-process fixture loading them. Live server's adapter list is `['freecad', 'calculix', 'knowledge', 'constraint', 'twin', 'project', 'memory', 'digikey']` — cadquery missing. Need to find why bootstrap skips it in the gateway container (env var? adapter_ids filter? docker config inconsistency?).

### G3 — memory pool contention
### G4 — extract_properties LLM-over-chunks fallback

## Phase 3-7 — pending
