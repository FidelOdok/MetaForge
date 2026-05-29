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

## Phase 2 — Gap fixes (next)

Order: G1 (`set_insight_store`) → G2 (cadquery in MCP bootstrap) → G3 (memory pool) → G4 (extract fallback).

## Phase 3-7 — pending
