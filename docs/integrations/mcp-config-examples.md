# MCP Config Examples

> **Status:** P1.11 Real Backends. Update this doc when the entrypoint
> CLI grows new flags or transports.

External MCP harnesses (Claude Code, Codex CLI, custom clients) drive
MetaForge tools through the standalone server shipped in MET-337:

```bash
python -m metaforge.mcp --transport {stdio,http,sse} [--port N] [--adapters …]
```

Three transports are supported. Pick the one that matches how your
harness connects.

## Claude Code (stdio subprocess)

Claude Code spawns each MCP server as a subprocess and frames JSON-RPC
on stdin/stdout. Drop a `.mcp.json` at the project root (the file
shipped in this repo is the canonical example):

```json
{
  "mcpServers": {
    "metaforge": {
      "command": "python",
      "args": ["-m", "metaforge.mcp", "--transport", "stdio"],
      "env": {
        "METAFORGE_ADAPTERS": "cadquery,calculix,knowledge"
      }
    }
  }
}
```

Launch Claude Code in the project directory; it picks up `.mcp.json`
automatically. Verify with `/mcp` — MetaForge should appear as
connected. Tool ids look like `cadquery.create_parametric`,
`knowledge.search`, etc.

### Optional auth (MET-338)

For non-local deployments, set `METAFORGE_MCP_API_KEY` on the launcher
side and a matching `METAFORGE_MCP_CLIENT_KEY` in the harness env:

```json
{
  "mcpServers": {
    "metaforge": {
      "command": "python",
      "args": ["-m", "metaforge.mcp", "--transport", "stdio"],
      "env": {
        "METAFORGE_MCP_API_KEY": "${METAFORGE_MCP_API_KEY}",
        "METAFORGE_MCP_CLIENT_KEY": "${METAFORGE_MCP_API_KEY}"
      }
    }
  }
}
```

The launcher checks both env vars at startup; mismatch emits an
`auth_error` JSON-RPC response and the subprocess exits cleanly. Open
mode (no enforcement) when neither var is set.

## Codex / generic harness (HTTP/SSE)

Harnesses that connect over HTTP rather than spawning a subprocess use
the same entrypoint with `--transport http` (or `sse`):

```bash
python -m metaforge.mcp --transport http --host 127.0.0.1 --port 8765
```

This binds to localhost by default — set `--host 0.0.0.0` only when
the server is reachable on a trusted network.

Codex CLI config snippet:

```toml
[[mcp_servers]]
name = "metaforge"
url  = "http://127.0.0.1:8765/mcp"
authorization = "Bearer ${METAFORGE_MCP_API_KEY}"
```

The HTTP `Authorization: Bearer <key>` header is enforced when
`METAFORGE_MCP_API_KEY` is set on the server side. `/health` is
intentionally exempt from auth so orchestrators can probe readiness
without credentials.

### Sample curl session

```bash
# 1. Launch server in HTTP mode
METAFORGE_MCP_API_KEY="dev-secret" \
  python -m metaforge.mcp --transport http --port 8765 &

# 2. Tool list
curl -sS http://127.0.0.1:8765/mcp \
  -H "Authorization: Bearer dev-secret" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":"1","method":"tool/list","params":{}}' \
  | jq '.result.tools | length'

# 3. Health (no auth required)
curl -sS http://127.0.0.1:8765/health | jq '.adapter_count'
```

### SSE (server-sent events)

For streaming tool-call results to a generic SSE client:

```bash
python -m metaforge.mcp --transport sse --port 8765
```

`GET /mcp/sse?request=<urlencoded JSON-RPC>` queues one request per
`request` query parameter; each response is emitted as a separate
`event: response` SSE block, terminated by `event: done`.

## Connecting MetaForge's own gateway to an external MCP server

The reverse direction — pointing the gateway's agents at an external
MCP server — uses the bridge factory shipped in MET-306:

```bash
# Gateway connects to a remote stdio MCP server (e.g. another
# MetaForge node, or a third-party harness exposing the same tools).
export METAFORGE_MCP_BRIDGE=stdio
export METAFORGE_MCP_SERVER_CMD="python -m metaforge.mcp --transport stdio"
export METAFORGE_MCP_CLIENT_KEY="${METAFORGE_MCP_API_KEY}"
forge-server  # or `uvicorn api_gateway.server:app`
```

`METAFORGE_MCP_BRIDGE=http` + `METAFORGE_MCP_SERVER_URL=http://…` is
the equivalent for HTTP. Either degrades gracefully to the in-process
registry bridge on connection failure unless
`METAFORGE_REQUIRE_MCP=true` forces a hard fail.

## Adapter selection

`METAFORGE_ADAPTERS` (comma-separated allow-list) and per-adapter
`METAFORGE_ADAPTER_<ID>_ENABLED=false` toggles control which adapters
load. Defaults to every known adapter; trim the set in resource-
constrained environments. Knowledge adapter requires a live
`KnowledgeService` — the gateway boot wires that automatically; the
standalone server skips it when no service is supplied.

## Related

- [MET-337](https://linear.app/metaforge/issue/MET-337) — standalone
  MCP server entrypoint.
- [MET-306](https://linear.app/metaforge/issue/MET-306) — gateway-side
  bridge factory + StdioTransport.
- [MET-338](https://linear.app/metaforge/issue/MET-338) — optional
  API-key auth on both transports.
- `docs/integrations/claude-code.md` (MET-341) — full Claude Code
  walkthrough.
- `docs/integrations/codex.md` (MET-342) — full Codex/generic
  walkthrough.
