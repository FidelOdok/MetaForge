"""End-to-end MCP integration tests (MET-477 follow-up).

These tests exercise the unified MCP server through its HTTP transport,
covering every registered tool group + per-vertical readiness scenarios.
Two run modes:

* In-process (default for CI): build the FastAPI app with
  ``metaforge.mcp.__main__.build_http_app`` against an in-memory adapter
  registry, drive it via ``httpx.AsyncClient(transport=ASGITransport)``.
* Live (opt-in via ``METAFORGE_MCP_URL``): point at a running server
  for smoke runs against fidel-dev or any deployed instance.

See ``conftest.py`` for the fixture surface; ``_helpers.py`` for the
``rpc()`` + ``parse_tool_result()`` utilities every test uses.
"""
