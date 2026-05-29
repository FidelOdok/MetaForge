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

### G1 — MCP server wires insight_store ✅ DONE (this PR)
- `metaforge/mcp/server.py:build_unified_server` now accepts `memory_insight_store` and forwards to `bootstrap_tool_registry`.
- `metaforge/mcp/__main__.py` gains `_build_insight_store()` + `_close_insight_store()` — pgvector when `DATABASE_URL` set, in-memory otherwise.
- `_bootstrap()` returns a 5-tuple; both stdio and HTTP paths unpack and close.
- `conftest.py` mirrors the wiring with an `InMemoryInsightStore` so the in-process fixture matches production.
- Test: `test_memory_tools.py` asserts `memory.list_insights` returns a clean `data.insights: []` envelope (pre-G1 raised -32001).
- Also asserts `memory.retrieve_similar_experience` returns `data.hits: []`.

### G2 — CadQuery in MCP bootstrap (next)
Surface check from the in-process test fixture showed cadquery IS already loaded in `build_unified_server` (registered via `tool_registry/bootstrap.py:_ADAPTER_REGISTRY`). The MET-477 smoke ran against a live MCP server where cadquery was apparently filtered out by `METAFORGE_ADAPTERS`. Verify the live server includes it after deploy; if not, debug the env-var allow-list.

### G3 — memory pool contention
### G4 — extract_properties LLM-over-chunks fallback

## Phase 3-7 — pending
