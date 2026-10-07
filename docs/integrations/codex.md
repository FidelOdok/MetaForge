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

## Install as a plugin

`integrations/codex/` is a generated Codex plugin (build output of
`scripts/build_integrations.py`). It brings the MCP server, an `AGENTS.md`
and the same skills as the Claude Code package: the 30 engineering skills,
shipped from each skill's detailed `PLUGIN.md`, plus the
`intent-to-verified-design` lifecycle skill (FORGE-533). Codex has no slash
commands, so the lifecycle rules (ask for values you were not given; propose
a flow with `template` and `operations` together; stop at the approval) live
in `AGENTS.md` and that skill. The package's README covers the marketplace
layout Codex expects.

Codex has no slash commands and no plugin hooks, so each workflow (`flow`,
`verify`, `replan`, ...) ships as a `<name>-workflow` skill, and the session
rules live in `AGENTS.md` (FORGE-539).

The plugin's `.mcp.json` points at `http://localhost:8765/mcp?profile=core`.
Codex has no install-time settings, so to work in another discipline edit the
`profile` in that URL: `mechanical` or `mechanical_product` for CAD,
`simulation` for FEA, `electronics` for KiCad and sourcing.

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

## Writes wait for a human

A tool that writes is held for approval when Codex reaches the
gateway as a remote caller. The check runs on the server's dispatch path,
so it applies whatever client asks and cannot be skipped by a client that
does not implement it.

A held call appears on the dashboard's **Approvals** page, tagged with
the caller and `source: mcp`. Approve it there and the tool runs;
reject it and it does not.

When Codex declared the `elicitation` capability at `initialize`, the
question is asked inside Codex instead, and the dashboard is only the
fallback. Over HTTP it travels on whichever stream the session has: a
`GET /mcp` stream if one is open, otherwise the held `tools/call`'s own
response, which the server switches to `text/event-stream` when the call
was sent with `Accept: application/json, text/event-stream` (FORGE-464).
The answer comes back as a POST and the tool result follows on the same
stream. See
[Over HTTP: which stream carries the question](../capability-matrix.md#over-http-which-stream-carries-the-question).

If it is not approved, the call comes back as a JSON-RPC error naming
which happened:

```json
{
  "code": -32001,
  "message": "twin.commit_geometry was not run: a reviewer rejected it.",
  "data": {"tool_id": "twin.commit_geometry", "code": "approval_required",
            "outcome": "rejected", "retryable": false}
}
```

`outcome` is `rejected`, `timed_out` (nobody answered within the window:
180s when progress is sent, 100s when not; see
[While a call waits on the dashboard](claude-code.md#while-a-call-waits-on-the-dashboard)),
`cancelled` (the hold was closed before anyone answered) or
`not_configured` (the server holds writes but has no approval gate wired,
so it refused rather than ran). None is worth retrying without a person
doing something first.

Running over **stdio on your own machine**, writes are not held by
default: there is nowhere to answer an approval in that transport yet.
That changes when a deployment sets `exempt_local_writes=False`.

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
