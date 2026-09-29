# Use MetaForge from Codex / generic MCP harness

> **Status:** P1.11 Real Backends. Walkthrough for harnesses that
> connect to MetaForge over HTTP or SSE rather than spawning a
> subprocess. For the Claude Code (stdio subprocess) path see
> [`claude-code.md`](claude-code.md).

The same `python -m metaforge.mcp` entrypoint that backs the Claude
Code subprocess also supports HTTP and SSE transports. Codex CLI,
custom MCP clients, and language-agnostic harnesses (curl, web UIs)
talk to it over HTTP `/mcp` and SSE `/mcp/sse`.

For a full config reference (every transport, env var, and the
reverse direction where the gateway connects out to an external MCP
server), see [`mcp-config-examples.md`](mcp-config-examples.md).

## HTTP vs SSE

| Concern | `--transport http` | `--transport sse` |
|---|---|---|
| Wire | One JSON-RPC request → one JSON response | Repeated JSON-RPC requests as `request=…` query params; responses streamed as SSE `data:` events |
| Use case | Codex CLI, curl scripts, programmatic clients | Streaming UIs, log-tail-style consumers, web pages with EventSource |
| Auth | `Authorization: Bearer <key>` header | Same |
| Health probe | `GET /health` (open) | Same |

Pick HTTP unless you specifically need server-sent events. Codex CLI
defaults to HTTP.

## 1. Launch in HTTP mode

```bash
# Local dev — open mode, default port 8765
python -m metaforge.mcp --transport http
```

For non-local use, set an API key. The launcher enforces it on every
`/mcp` call; `/health` stays open so orchestrators can probe
readiness without credentials.

```bash
export METAFORGE_MCP_API_KEY="$(openssl rand -hex 32)"
python -m metaforge.mcp \
  --transport http \
  --host 0.0.0.0 \
  --port 8765
```

Bind to `0.0.0.0` only on a trusted network. The launcher defaults
to `127.0.0.1`.

The launcher logs `mcp_http_ready host=… port=… auth_enforced=true|false`
on startup so operators see the auth posture in their own logs.

## 2. Configure Codex CLI

Codex's MCP server config lives in its TOML config file (typically
`~/.codex/config.toml`):

```toml
[[mcp_servers]]
name = "metaforge"
url  = "http://127.0.0.1:8765/mcp"
authorization = "Bearer ${METAFORGE_MCP_API_KEY}"
```

Codex reads `${VAR}` placeholders from the calling shell's env. Open
mode (no `METAFORGE_MCP_API_KEY` on the server side) means the
`authorization` line can be omitted.

Restart Codex CLI after editing. List MetaForge tools from inside
Codex with the harness's standard `mcp tools` (or equivalent)
command.

## 3. Generic MCP harness — sample curl session

The HTTP endpoint is plain JSON-RPC over POST. Any language with an
HTTP client can drive it:

```bash
KEY="${METAFORGE_MCP_API_KEY:-}"
AUTH=()
[ -n "$KEY" ] && AUTH=(-H "Authorization: Bearer $KEY")

# 1. Health (no auth required)
curl -sS http://127.0.0.1:8765/health | jq .

# 2. List every tool
curl -sS http://127.0.0.1:8765/mcp "${AUTH[@]}" \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":"1","method":"tool/list","params":{}}' \
  | jq '.result.tools | length'

# 3. Call a tool
curl -sS http://127.0.0.1:8765/mcp "${AUTH[@]}" \
  -H 'Content-Type: application/json' \
  -d '{
    "jsonrpc":"2.0",
    "id":"2",
    "method":"tool/call",
    "params":{
      "tool_id":"cadquery.create_parametric",
      "arguments":{
        "shape_type":"box",
        "parameters":{"width":50,"length":30,"height":10},
        "output_path":"/tmp/box.step"
      }
    }
  }' \
  | jq .
```

A successful tool call returns:

```json
{
  "jsonrpc": "2.0",
  "id": "2",
  "result": {
    "tool_id": "cadquery.create_parametric",
    "status": "success",
    "data": {
      "cad_file": "/tmp/box.step",
      "volume_mm3": 15000.0,
      "surface_area_mm2": 4600.0,
      "bounding_box": {"width": 50, "length": 30, "height": 10},
      "parameters_used": {...}
    },
    "duration_ms": 234.7
  }
}
```

Error responses follow JSON-RPC convention:

```json
{
  "jsonrpc": "2.0",
  "id": "2",
  "error": {
    "code": -32601,
    "message": "Tool not found: nonexistent.tool",
    "data": {"tool_id": "nonexistent.tool"}
  }
}
```

The error codes you'll see:

| Code | Meaning |
|---|---|
| -32600 | Invalid JSON-RPC request |
| -32601 | Unknown tool id (or unknown method) |
| -32001 | Tool execution failed (server-side) |
| -32002 | Auth denied (when `METAFORGE_MCP_API_KEY` is set) |

## 4. SSE — streaming responses

If your harness expects server-sent events, launch with `--transport
sse` and consume `/mcp/sse` with a queryable URL:

```bash
python -m metaforge.mcp --transport sse --port 8765
```

```bash
# URL-encode each JSON-RPC request and pass as `request=…`
REQ=$(jq -rsR @uri <<<'{"jsonrpc":"2.0","id":"1","method":"tool/list","params":{}}')
curl -N -H "Authorization: Bearer ${METAFORGE_MCP_API_KEY}" \
  "http://127.0.0.1:8765/mcp/sse?request=${REQ}"
```

Each response arrives as an `event: response` block, terminated by a
single `event: done` block.

## stdio vs HTTP/SSE — when to pick which

| | Claude Code (stdio) | Codex / generic (HTTP/SSE) |
|---|---|---|
| Process model | Spawned subprocess per session | Long-running daemon |
| Discovery | Reads `.mcp.json` automatically | TOML / config-file dance per harness |
| Scope | Single user, single project | Multi-tenant; multiple harnesses point at one server |
| Auth | Optional, via env propagation | Required for any non-local deploy |
| Use when | Local dev with Claude Code | Sharing one MetaForge instance across teammates / CI / agents |

## Auth recipes

* **Open mode (default).** No env vars set. Server accepts every
  request. Local-dev only.
* **Per-deployment shared secret.** Generate once, set
  `METAFORGE_MCP_API_KEY` on the server side and distribute to
  trusted clients out of band. Rotate with `openssl rand -hex 32`.
* **Per-client tokens.** Out of scope today — the launcher checks
  one shared key. If you need per-client identity, front the
  HTTP transport with an authenticating reverse proxy (Caddy /
  nginx with a JWT validator) and set
  `METAFORGE_MCP_API_KEY` to the proxy-shared secret.

## Health, observability, and ops

* `GET /health` returns the server's roll-up (service name, status,
  adapter and tool counts). Use this in liveness / readiness probes.
* The launcher logs every connection event on stderr with structured
  fields: `mcp_auth_denied transport=http reason=…`,
  `mcp_http_ready auth_enforced=…`, etc. Pipe into your usual log
  aggregator.
* MET-326's retrieval-quality histograms (`metaforge_retrieval_*`)
  surface tool-call quality when knowledge tools are exercised; the
  same dashboard applies whether the calls came over stdio, HTTP, or
  SSE.

## Keeping the tool list a size Codex can use

All 97 tools are served by default. If that is more than you want in
context, start the server with a profile:

```bash
python -m metaforge.mcp --transport http --profile mechanical
```

`core`, `mechanical`, `simulation`, `electronics` and `robotics` are each
between 20 and 40 tools. An unknown name stops the server rather than
quietly serving everything.

Whatever the reason a tool is not in the list, the response says so.
An adapter whose container is down contributes nothing, but appears under
`_meta.unavailableAdapters`; a profile naming a tool no loaded adapter
registers appears under `_meta.profile.missing`. `_meta` is absent when
there is nothing to report, so seeing it at all means something is worth
looking at — `python -m metaforge.mcp --transport http` with no
adapters down and no profile set returns none.

## Tool annotations and unknown-tool errors

Every tool on `tools/list` carries the MCP annotation hints, so Codex
can tell a read from a write before it calls anything:

```json
{
  "name": "twin.get_node",
  "annotations": {
    "readOnlyHint": true,
    "destructiveHint": false,
    "idempotentHint": true,
    "openWorldHint": false
  }
}
```

The classification is deliberate rather than derived from the tool's
name, and a tool nobody has classified inherits MCP's safe defaults
(`readOnlyHint: false`, `destructiveHint: true`). One case is worth
knowing: `twin.query_cypher` is read-only until the gateway is started
with `--allow-twin-mutations`, after which it is not — the hint is
computed per request from that flag, so it always matches what the
server will enforce.

Getting a tool id slightly wrong is recoverable. Separator mistakes
resolve on their own, so `twin_get_node` and `twin/get_node` both reach
`twin.get_node`; an alias that matches two tools is refused rather than
guessed at. When an id matches nothing, the error names the closest real
tools, in the message and in `error.data.did_you_mean`:

```json
{
  "code": -32601,
  "message": "Tool not found: twin.get_nodes. Did you mean: twin.get_node",
  "data": {
    "tool_id": "twin.get_nodes",
    "did_you_mean": ["twin.get_node"],
    "tool_count": 97
  }
}
```

## Troubleshooting

### `curl: (7) Failed to connect`

Server isn't listening. Confirm with `curl http://127.0.0.1:8765/health`
from the same machine. Check the launcher's stderr for bind failures
(another process on 8765, or `--host 0.0.0.0` blocked by a firewall).

### 401 `auth_error reason=missing_key`

You set `METAFORGE_MCP_API_KEY` on the server but the request didn't
include `Authorization: Bearer …`. Either include the header or
unset the env var on the server.

### 401 `auth_error reason=mismatch`

The header value doesn't equal `METAFORGE_MCP_API_KEY`. Constant-time
compare, no length leak — but trim trailing newlines before comparing
(`openssl rand` doesn't add one; `head /dev/urandom | base64` does).

### Tool list works, every `tool/call` returns -32001

The adapter's handler is failing. Check the launcher's stderr — the
launcher logs `tool_handler_failed` with the tool id, duration, and
the structured details payload that came back. Common causes:

* CadQuery / FreeCAD not installed; the manifest registers but the
  handler raises on first invocation.
* CalculiX binary not on PATH; same shape.
* Knowledge backend (Postgres / pgvector) unreachable.

## Related

* [`claude-code.md`](claude-code.md) — Claude Code subprocess
  walkthrough (stdio).
* [`mcp-config-examples.md`](mcp-config-examples.md) — full config
  reference.
* [MET-337](https://linear.app/metaforge/issue/MET-337) — standalone
  MCP server entrypoint.
* [MET-338](https://linear.app/metaforge/issue/MET-338) — optional
  API-key auth on transports.
