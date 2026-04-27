"""Standalone MCP server entrypoint (MET-337).

Aggregates every enabled adapter from ``tool_registry.bootstrap`` into a
single MCP-protocol-speaking process so external harnesses (Claude Code,
Codex, generic clients) can drive the unified MetaForge tool surface
without spinning up the full FastAPI gateway.

Routing is by tool-id prefix: ``knowledge.search`` → ``KnowledgeServer``,
``cadquery.create_parametric`` → ``CadqueryServer``, etc. The unified
server owns ``tool/list`` (which fans across every adapter) and
``tool/call`` (which dispatches by prefix); ``health/check`` returns a
roll-up of every adapter's status.

Two transports today:

* ``stdio`` — line-delimited JSON-RPC on stdin/stdout. The Claude Code
  default and the harness MET-340's E2E test exercises.
* ``http`` — minimal FastAPI app on 127.0.0.1 by default. POST
  ``/mcp`` accepts a JSON-RPC request and returns the response;
  GET ``/mcp/sse`` streams server-sent events for tool-call results
  (Codex / generic clients).

API-key auth is wired in MET-338 once this lands. Not part of this
ticket.
"""

from metaforge.mcp.server import UnifiedMcpServer, build_unified_server

__all__ = ["UnifiedMcpServer", "build_unified_server"]
