# MCP Protocol Specification

> **Version**: 0.1 (Phase 0 — Spec & Design)
> **Status**: Draft
> **Last Updated**: 2026-03-02
> **Depends on**: [`architecture.md`](architecture.md), [`twin_schema.md`](twin_schema.md), [`skill_spec.md`](skill_spec.md)
> **Referenced by**: [`roadmap.md`](roadmap.md), [`governance.md`](governance.md)

## 1. Overview

The Model Context Protocol (MCP) layer is the **exclusive pathway** for tool access in MetaForge. No agent, skill, or other component ever calls an engineering tool directly. All tool invocations pass through the MCP protocol, which provides:

- **Uniform interface**: Every tool (KiCad, FreeCAD, CalculiX, SPICE) is accessed through the same JSON-RPC 2.0 protocol.
- **Container isolation**: Tools run in Docker containers with strict resource and filesystem controls.
- **Observability**: Every tool call is traced, logged, and metered via OpenTelemetry.
- **Health tracking**: The tool registry monitors adapter health and routes calls to healthy instances.

### Architecture Summary

```
Skill (handler.py)
    │
    ▼
MCP Bridge (skill_registry/mcp_bridge.py)
    │
    ▼
MCP Client (mcp_core/client.py)
    │
    ▼
Wire Protocol (JSON-RPC 2.0 over stdio or HTTP)
    │
    ▼
MCP Tool Server (tool adapter, inside Docker)
    │
    ▼
Engineering Tool (KiCad, FreeCAD, CalculiX, SPICE)
```

---

## 2. Client-Server Architecture

### MCP Client

The MCP Client lives in `mcp_core/client.py` and manages connections to tool adapter servers.

```python
"""MCP client for tool communication."""

from pydantic import BaseModel
from uuid import UUID, uuid4


class McpClient:
    """
    Manages connections to MCP tool servers and dispatches tool calls.

    Each tool adapter runs as an MCP server (inside a Docker container).
    The client connects via stdio (subprocess) or HTTP, depending on config.
    """

    async def connect(self, adapter_id: str) -> None:
        """Establish connection to a tool adapter server."""
        ...

    async def disconnect(self, adapter_id: str) -> None:
        """Close connection to a tool adapter server."""
        ...

    async def call_tool(self, request: "ToolCallRequest") -> "ToolCallResult":
        """
        Send a tool/call request and wait for the result.

        Handles timeout, retry (if idempotent), and error mapping.
        """
        ...

    async def list_tools(self, adapter_id: str | None = None) -> list["ToolManifest"]:
        """
        Discover available tools from one or all connected adapters.
        """
        ...

    async def health_check(self, adapter_id: str) -> "HealthStatus":
        """Check the health of a specific adapter."""
        ...
```

### MCP Tool Server

Each tool adapter implements an MCP-compatible server. The server receives JSON-RPC requests, delegates to the underlying tool, and returns structured results.

```python
"""Base class for MCP tool servers (adapters)."""


class McpToolServer:
    """
    Base class for tool adapter MCP servers.

    Subclass this to create a new tool adapter. Implement:
    1. register_tools() — declare available tools.
    2. Tool handler methods — one per tool.
    """

    def __init__(self, adapter_id: str, version: str) -> None:
        self.adapter_id = adapter_id
        self.version = version
        self._tools: dict[str, "ToolManifest"] = {}

    def register_tool(self, manifest: "ToolManifest", handler) -> None:
        """Register a tool with its manifest and handler function."""
        self._tools[manifest.tool_id] = manifest
        # ... bind handler

    async def handle_request(self, raw_message: str) -> str:
        """Parse JSON-RPC request, dispatch to handler, return JSON-RPC response."""
        ...

    async def start_stdio(self) -> None:
        """Start the server in stdio mode (reads stdin, writes stdout)."""
        ...

    async def start_http(self, host: str = "0.0.0.0", port: int = 8080) -> None:
        """Start the server in HTTP mode."""
        ...
```

---

## 3. Wire Protocol

MCP uses **JSON-RPC 2.0** as the wire protocol. Messages are exchanged over **stdio** (for local tool containers) or **HTTP** (for remote tool servers).

### Transport Modes

| Mode | Use Case | Connection |
|------|----------|-----------|
| **stdio** | Local Docker containers (default) | Client spawns container process, communicates via stdin/stdout |
| **HTTP** | Remote tool servers, shared instances | Client sends POST requests to `http://<host>:<port>/rpc` |

#### Streamable HTTP on the unified sidecar

The unified server (`python -m metaforge.mcp --transport http`) speaks MCP
Streamable HTTP at `/mcp`:

| Request | What it does |
|---|---|
| `POST /mcp` with a JSON-RPC request | dispatches it; the answer is `application/json`, or `204` for a notification. `initialize` issues an `Mcp-Session-Id` the client echoes afterwards |
| `POST /mcp` with a JSON-RPC response | the client answering a request the server sent (`elicitation/create`), routed by id and acknowledged `202` |
| `GET /mcp` with `Mcp-Session-Id` | a long-lived SSE stream the server pushes requests down (FORGE-423) |
| `DELETE /mcp` with `Mcp-Session-Id` | ends the session |

A `tools/call` that is held for approval can also be answered as
`text/event-stream` on its own POST (FORGE-464). This happens only when
the session declared form `elicitation` at `initialize` on revision
`2025-06-18` or later, has no `GET /mcp` stream open, and sent the call
with `text/event-stream` in `Accept`. The stream carries the
`elicitation/create` request, then the tool result, then closes. A call
that is not held keeps its JSON response, so clients that never open
`GET /mcp` (Claude Code among them) can still be asked inline. Routing
rules and outcomes are in
[Over HTTP: which stream carries the question](capability-matrix.md#over-http-which-stream-carries-the-question).

#### Browser origins (FORGE-604)

Every request to `/mcp` (POST, GET and DELETE) and `/mcp/sse` has its
`Origin` header checked, as the Streamable HTTP transport requires. A
request whose origin is not allowed gets `403` with a JSON-RPC error
(`-32600`, "Origin not allowed", no `id`), a `mcp_origin_rejected` log line
naming the origin, and a count on `metaforge_mcp_origin_rejected_total`,
which the `McpOriginRejected` alert watches.

Allowed:

- requests with no `Origin` at all. Only browsers send one, so Claude Code,
  Codex, the design-flow worker and other servers are unaffected;
- loopback origins (`localhost`, `*.localhost`, `127.0.0.0/8`, `[::1]`), any
  port;
- `https://claude.ai` and `https://claude.com`, for the web connector;
- this server's public origin, when `METAFORGE_OAUTH_ISSUER` names it;
- anything listed in `METAFORGE_MCP_ALLOWED_ORIGINS` (comma separated, for
  example `https://ui.example.com:8443`). `*` turns the check off and the
  server logs that at start-up.

The origin is not compared with the `Host` header. That is the check DNS
rebinding defeats: a page re-points its own name at this machine, and its
`Origin` and the request's `Host` then agree. `/health`, the OAuth pages and
`/.well-known/*` are not checked.

#### Protocol revisions (FORGE-599)

The server speaks the handshake revisions `2024-11-05`, `2025-06-18` and
`2025-11-25`, and the handshake-free revision `2026-07-28` (FORGE-592).
`initialize` echoes the handshake revision a client asks for when it is one
of these; a client ahead of us gets `2025-11-25`. The lists live in
`mcp_core/protocol_versions.py`.

Revision `2026-07-28` drops the handshake: each request names its revision
in `params._meta["io.modelcontextprotocol/protocolVersion"]` and, over HTTP,
in the `MCP-Protocol-Version` header. The server handles both shapes on the
same endpoint:

- `initialize` takes the handshake path, exactly as before.
- A request whose `_meta` names a revision we speak is served as before.
- A request whose `_meta` names any other revision is refused with the
  spec's `UnsupportedProtocolVersionError` (`-32022`, and HTTP `400`), whose
  `data.supported` lists our revisions newest first.
- Over HTTP, a `MCP-Protocol-Version` header that disagrees with the
  `_meta` revision is refused with `HeaderMismatch` (`-32020`, HTTP `400`).
  A header with no `_meta` revision (a `2025-06-18` client) is not a
  mismatch.

`2026-07-28` is served by default. `METAFORGE_MCP_MODERN=off` on the
sidecar stops serving it (a rollback switch); a client asking for it then
gets the error above and a dual-era client falls back to the handshake. The
FORGE-600 preview switch, `METAFORGE_MCP_MODERN_PREVIEW`, no longer changes
anything.

A `2026-07-28` request is served on its own, without a session:

- `params._meta` must carry `io.modelcontextprotocol/protocolVersion` and
  `io.modelcontextprotocol/clientCapabilities`; a request without the
  capabilities is refused with `-32602` (HTTP `400`). The client's revision
  and capabilities hold for that request only and never replace what a
  handshake client declared.
- `server/discover` answers with `supportedVersions` (newest first),
  `capabilities` and `instructions`.
- Every result carries `resultType: "complete"` and
  `_meta["io.modelcontextprotocol/serverInfo"]`. `server/discover`, the
  `*/list` methods and `resources/read` also carry `ttlMs` and
  `cacheScope` (`private`: lists depend on the connection's profile and
  caller; reads are never fresh).
- Resource not found is `-32602`; the handshake revisions keep `-32002`.
- Over HTTP the request must carry `MCP-Protocol-Version` and `Mcp-Method`,
  and `Mcp-Name` for `tools/call`, `prompts/get` and `resources/read`
  (base64 values in the `=?base64?...?=` form are decoded), each matching
  the body; otherwise `HeaderMismatch` (`-32020`, HTTP `400`). An unknown
  method is HTTP `404` with `-32601`. No `Mcp-Session-Id` is issued.
- The server never sends such a request a question of its own; that
  revision forbids server-initiated requests. The question comes back inside
  the result instead (below).

#### Session handles instead of `Mcp-Session-Id` (FORGE-602)

`2026-07-28` has no protocol session, so nothing ties one call to the next.
Two things used to ride on the session: the project `project.open` scopes
later calls to, and the record `session.start` opens. For a `2026-07-28`
caller they ride on a handle instead:

- `project.open` and `session.start` mint a handle when the caller passes
  none, report it in the result's
  `_meta["com.metaforge/sessionHandle"]`, and add a line to the result text
  saying to pass it on.
- Every tool lists an optional `metaforge_session` argument for these
  callers. A call that passes the handle runs as that session: its project
  scope and session record apply. The argument is taken out before the tool
  sees it. A value that is not a handle is refused with `-32602`.
- A handle is a session id, not a credential: it carries scope, never
  authority, exactly as `X-MetaForge-Session` does. That header still works
  for any client that sends it, and handshake clients keep `Mcp-Session-Id`.

#### `subscriptions/listen` (FORGE-603)

`2026-07-28` replaced the `GET /mcp` stream and `resources/subscribe` with
`subscriptions/listen`. Nothing this server offers changes while it is up:
the tool, prompt and resource lists are fixed once it starts, and it does
not report per-resource updates. So it honours none of the notification
types. It answers with `notifications/subscriptions/acknowledged` carrying
an empty `notifications` filter (which tells the client nothing it asked
for is supported), then closes the subscription gracefully with a
`resultType: "complete"` result. Both carry
`_meta["io.modelcontextprotocol/subscriptionId"]`. Over HTTP the two arrive
as one `text/event-stream` response; on stdio the acknowledgement is written
first. A malformed filter is `-32602`.

#### Error codes for `2026-07-28` (FORGE-607)

`2026-07-28` marks `-32000` to `-32019` as legacy, and this server's own
codes all sit there: tool execution `-32001`, unavailable `-32003`, read
error `-32005`, timeout `-32006`, auth `-32007`. A `2026-07-28` caller never
gets them:

- A `tools/call` refused that way (a held write rejected or with no gate, a
  tool that failed, the approval ledger down) is a normal result with
  `isError: true`, as the spec recommends for a tool failure. The reason is
  the result text, which is what a model reads, and the code and details
  are in `_meta["com.metaforge/error"]` (`code`, `legacyCode`, `data`).
- Any other method's code moves out of the reserved range by 1000
  (`-32001` becomes `-31001`), with the original in `error.data.legacyCode`.
- Resource not found is still `-32602`.

Handshake clients keep the codes in the table above.

#### Questions returned inside results (FORGE-601)

For a `2026-07-28` `tools/call`, `prompts/get` or `resources/read` that
needs a person, the call returns early with `resultType: "input_required"`:

```json
{"resultType": "input_required",
 "inputRequests": {"q1": {"method": "elicitation/create",
   "params": {"mode": "form", "message": "...", "requestedSchema": {...}}}},
 "requestState": "<opaque>"}
```

The client asks the person and retries the same call (a new JSON-RPC id)
with their answers in `params.inputResponses` (keyed `q1`, `q2`, ... in the
order asked) and `params.requestState` echoed unchanged. The retry runs the
call again from the start; each question finds its answer.

- **Held writes.** The approval entry is opened on the first pass, before
  the question, as for any inline approval (FORGE-473). The retry closes the
  same entry with the person's answer and an approver, then runs or refuses
  the write. A decision made in the dashboard in between wins. A retry
  without the answer asks again on the same entry.
- **Design-flow gates.** `flow.await_gate` asks through the same mechanism,
  so a `2026-07-28` client decides a gate in its chat as a handshake client
  does.
- **`requestState`** is HMAC-signed and bound to the caller, the method, a
  digest of the call's own parameters and a 15 minute expiry. A forged,
  expired or mismatched state, `inputResponses` without a state, or an
  approval used a second time is refused with `-32602`, and nothing runs.
  Set `METAFORGE_MCP_STATE_KEY` to keep outstanding questions answerable
  across a restart; without it a random per-process key is used.
- A client that does not declare form elicitation in the request's
  `clientCapabilities`, or a sidecar with no approval ledger, gets no
  question: the held write waits in the dashboard and `forge approvals`.

From `2025-11-25` a client can declare elicitation as `{"form": {}}`,
`{"url": {}}` or both; an empty `{}` still means form. Every question this
server asks is a form, so a client that declares only `url` is not asked
inline and its held calls go to the dashboard.

#### Held calls report themselves (FORGE-465)

A `tools/call` held for the **dashboard** (the client cannot be asked
inline) is not silent until it ends:

* **Early signal.** If the call carries `params._meta.progressToken` and the
  transport can reach the client for that call, the server sends
  `notifications/progress` as soon as the hold exists. `message` names the
  approval id and the dashboard Approvals page (a link when
  `METAFORGE_DASHBOARD_URL` is set); `total` is the hold window in seconds.
  It repeats every `METAFORGE_APPROVAL_PROGRESS_INTERVAL_SECONDS` (default
  10) with `progress` increasing, so a client that resets its timeout on
  progress keeps waiting. Over HTTP, a call with a `progressToken` and
  `text/event-stream` in `Accept` is eligible for its own SSE stream whether
  or not the session declared `elicitation`; the response switches to SSE
  only once something is sent. Over stdio the notification is written to
  stdout like any other message.
* **Window.** With a progress channel the hold waits
  `METAFORGE_APPROVAL_HOLD_PROGRESS_SECONDS` (default 180). Without one it
  waits `METAFORGE_APPROVAL_HOLD_SECONDS` (default 100), below common client
  tool timeouts (Claude Code: 120), so the server answers before the client
  gives up. The window travels on the approval ask and sets the hold's
  ledger deadline (FORGE-466). A gate constructed with an explicit
  `timeout_seconds` caps it.
* **Outcome.** An unapproved call is a JSON-RPC error with
  `data.code = "approval_required"` and `data.outcome` one of `rejected`,
  `timed_out`, `cancelled` or `not_configured`, plus `approval_id`, `route`
  (`dashboard` or `elicitation`), `where`, `held_seconds` and
  `window_seconds` when known. `message` states the same facts, because it
  is what a client shows the model.

#### Expired inline questions are withdrawn (FORGE-472)

An inline (`elicitation`) hold uses the same window rule as a dashboard
hold: `METAFORGE_APPROVAL_HOLD_SECONDS` (default 100) without a progress
channel, `METAFORGE_APPROVAL_HOLD_PROGRESS_SECONDS` (default 180) with one,
in which case progress is sent while the prompt is open. The window is
logged as `window_seconds` on `mcp_tool_call_held_for_approval` and stated
in the `elicitation/create` message.

* **Withdrawal.** When the server stops waiting on an `elicitation/create`
  for any reason (window ended, call cancelled, call stream closed), it
  sends `notifications/cancelled` with `params.requestId` set to that
  request's id and a `reason`. Over HTTP it goes on the call's own stream
  while that is open, before the result, else on an open `GET /mcp`; over
  stdio it is written to stdout. The server logs
  `mcp_elicitation_withdrawn` with `delivered`.
* **Late answers.** A response to a withdrawn request is acknowledged
  (`202` over HTTP), logged as `mcp_elicitation_late_response_ignored`, and
  never applied. The call already ended as `timed_out` without running.

### Message Format

All messages follow the JSON-RPC 2.0 specification:

```json
{
  "jsonrpc": "2.0",
  "id": "<request-id>",
  "method": "<method-name>",
  "params": { ... }
}
```

---

## 4. Message Types

### 4.1 `tool/list` — Discover Available Tools

**Request**:

```json
{
  "jsonrpc": "2.0",
  "id": "req-001",
  "method": "tool/list",
  "params": {
    "capability": "stress_analysis"
  }
}
```

**Response**:

```json
{
  "jsonrpc": "2.0",
  "id": "req-001",
  "result": {
    "tools": [
      {
        "tool_id": "calculix.run_fea",
        "adapter_id": "calculix",
        "name": "Run FEA Analysis",
        "description": "Execute finite element analysis using CalculiX solver",
        "capability": "stress_analysis",
        "input_schema": { ... },
        "output_schema": { ... },
        "phase": 1,
        "resource_limits": {
          "max_memory_mb": 2048,
          "max_cpu_seconds": 600,
          "max_disk_mb": 512
        }
      }
    ]
  }
}
```

### 4.2 `tool/call` — Invoke a Tool

**Request**:

```json
{
  "jsonrpc": "2.0",
  "id": "req-002",
  "method": "tool/call",
  "params": {
    "tool_id": "calculix.run_fea",
    "arguments": {
      "mesh_file": "/workspace/mesh/bracket.inp",
      "load_case": "static_load_1",
      "analysis_type": "static_stress"
    },
    "timeout_seconds": 300,
    "trace_id": "abc-123-def"
  }
}
```

**Response (success)**:

```json
{
  "jsonrpc": "2.0",
  "id": "req-002",
  "result": {
    "tool_id": "calculix.run_fea",
    "status": "success",
    "data": {
      "max_von_mises": {
        "bracket_body": 145.2,
        "bracket_mount": 89.7
      },
      "solver_time": 12.5,
      "mesh_elements": 45000
    },
    "duration_ms": 12832,
    "output_files": [
      "/workspace/results/stress_output.frd"
    ]
  }
}
```

### 4.3 `tool/result` — Streaming Results (Optional)

For long-running tools, the server can send progress updates:

```json
{
  "jsonrpc": "2.0",
  "method": "tool/result",
  "params": {
    "request_id": "req-002",
    "progress": 0.65,
    "message": "Solving step 3/5..."
  }
}
```

### 4.4 `tool/error` — Error Response

> **On the MCP dialect (`tools/call`), a tool that ran and refused is not an
> error response at all (FORGE-419).** It comes back as a normal result with
> `isError: true`, the reason as text content, and `_meta.error` carrying
> `toolId / details / durationMs` — plus `_meta.callId`, so a failed call
> stays as citable as a successful one. See §4.4.1. The envelope below is the
> legacy `tool/call` dialect, whose callers already read `data.details`.

```json
{
  "jsonrpc": "2.0",
  "id": "req-002",
  "error": {
    "code": -32001,
    "message": "Tool execution failed",
    "data": {
      "error_type": "TOOL_EXECUTION_ERROR",
      "tool_id": "calculix.run_fea",
      "details": "CalculiX solver exited with code 1: mesh file not found",
      "duration_ms": 450
    }
  }
}
```

#### 4.4.1 `tools/call` — a tool that ran and refused (FORGE-419)

```json
{
  "jsonrpc": "2.0",
  "id": "req-002",
  "result": {
    "content": [
      {
        "type": "text",
        "text": "twin.query_cypher failed: call context is scoped to project 7b8a but no 'project_id' parameter was bound. Add WHERE n.project_id = $project_id and pass {'project_id': ...}"
      }
    ],
    "isError": true,
    "_meta": {
      "callId": "3cb7ee97145e4726",
      "error": {
        "toolId": "twin.query_cypher",
        "details": "call context is scoped to project 7b8a but no 'project_id' parameter was bound. …",
        "durationMs": 4.1
      }
    }
  }
}
```

This is the MCP-recommended shape, and the reason it changed is concrete.
The refusal above says exactly how to fix the query. It used to live only in
JSON-RPC `error.data.details`, and Claude Code shows `error.message` — the
constant string `"Tool execution failed"`. The agent retried blind, failed
the same way, gave up, and reported the tool as broken.

**Protocol errors stay JSON-RPC errors.** An unknown method or unknown tool
is not something the model can fix by changing its arguments, and the
unknown-tool error already carries a did-you-mean. The split is "did the
tool run?", not "was there a problem".

#### 4.4.2 Next-step hints and missing-field errors (FORGE-518)

`twin.stage_work_product_file` returns its staged `file_path` plus a
`next_step` string naming `freecad.import_step` (with that `file_path`) and
`freecad.describe_step_file`, so a model loads the stored part instead of
recreating it. `twin.commit_geometry` called with no geometry fails with
`Missing: session_id, obj_id, step_base64` listing exactly the absent fields
and what to pass.

### 4.5 `health/check` — Adapter Health

**Request**:

```json
{
  "jsonrpc": "2.0",
  "id": "req-003",
  "method": "health/check",
  "params": {}
}
```

**Response**:

```json
{
  "jsonrpc": "2.0",
  "id": "req-003",
  "result": {
    "adapter_id": "calculix",
    "status": "healthy",
    "version": "0.1.0",
    "tools_available": 3,
    "uptime_seconds": 3600,
    "last_invocation": "2026-03-02T10:15:00Z"
  }
}
```

#### Roll-up on the unified server

Sent to the unified server rather than to one adapter, `health/check`
returns an aggregate. The server answers it by sending this same request
to every adapter it has loaded, with a three-second timeout each, and
reporting what came back:

```json
{
  "service": "metaforge-mcp",
  "status": "degraded",
  "version": "0.1.0",
  "uptime_seconds": 3600.0,
  "adapter_count": 3,
  "tool_count": 100,
  "unreachable_adapters": ["calculix"],
  "detail": "1 of 3 adapters did not answer: calculix. Their tools are registered but calls to them will fail.",
  "adapters": [
    {"adapter_id": "twin", "version": "0.1.0", "tools_registered": 12, "reachable": true},
    {"adapter_id": "calculix", "version": "0.1.0", "tools_registered": 5, "reachable": false,
     "error": "adapter container is down (-32001)"}
  ]
}
```

The same report carries `client.can_elicit` (see
[Answering in the harness](capability-matrix.md#answering-in-the-harness)),
`auth` (what the transport in front of the server
enforces: `open`, `api_key`, `oauth`, `api_key+oauth`, or `unknown` when
the transport declared nothing) and `client` (the `clientInfo` from the
`initialize` handshake, plus `protocol_skew` when the revision the client
asked for is not the one the server pinned). Both are described under
[What is protecting this connection](capability-matrix.md#what-is-protecting-this-connection).

The report also carries `llm_usage_24h` (FORGE-476): calls, prompt,
completion and cached-input tokens and cost over the trailing 24 hours, with
`by_phase`, `by_role` and `by_model` breakdowns. `available: false` plus a
`reason` means the usage store could not be read, which is not the same as
nothing having been spent; a non-zero `calls_unpriced` means `cost_usd` is a
lower bound. Details are in
[Token and cost accounting](architecture/robust-harness-design.md#token-and-cost-accounting-forge-476).

`status` is `healthy` only if every adapter answered, `degraded`
otherwise. `tools_registered` is a registry count and does not shrink
when an adapter goes down — `reachable` is the field that says whether
those tools can actually be called. `unreachable_adapters` and `detail`
are present only when something is wrong, so their absence is itself the
healthy signal.

Over HTTP this is a 200 in both states: the MCP server is up and
reporting accurately on its dependencies. See
[Health is measured, not asserted](capability-matrix.md#health-is-measured-not-asserted).

---

## 5. Pydantic Message Schemas

All MCP messages are validated using Pydantic models in `mcp_core/schemas.py`.

```python
"""Pydantic schemas for MCP protocol messages."""

from datetime import datetime
from pydantic import BaseModel, Field
from typing import Any


# --- Requests ---

class ToolListRequest(BaseModel):
    capability: str | None = None


class ToolCallRequest(BaseModel):
    tool_id: str = Field(..., description="Tool identifier (e.g., 'calculix.run_fea')")
    arguments: dict[str, Any] = Field(default_factory=dict, description="Tool parameters")
    timeout_seconds: int = Field(default=120, ge=1, le=3600)
    trace_id: str | None = Field(default=None, description="OpenTelemetry trace ID")


class HealthCheckRequest(BaseModel):
    pass


# --- Responses ---

class ResourceLimits(BaseModel):
    max_memory_mb: int = 1024
    max_cpu_seconds: int = 300
    max_disk_mb: int = 256


class ToolManifest(BaseModel):
    tool_id: str
    adapter_id: str
    name: str
    description: str
    capability: str
    input_schema: dict = Field(default_factory=dict)
    output_schema: dict = Field(default_factory=dict)
    phase: int = 1
    resource_limits: ResourceLimits = Field(default_factory=ResourceLimits)


class ToolListResult(BaseModel):
    tools: list[ToolManifest]


class ToolCallResult(BaseModel):
    tool_id: str
    status: str  # "success" or "error"
    data: dict[str, Any] = Field(default_factory=dict)
    duration_ms: float = 0
    output_files: list[str] = Field(default_factory=list)


class ToolProgress(BaseModel):
    request_id: str
    progress: float = Field(ge=0.0, le=1.0)
    message: str = ""


class HealthStatus(BaseModel):
    adapter_id: str
    status: str  # "healthy", "degraded", "unhealthy"
    version: str
    tools_available: int
    uptime_seconds: float
    last_invocation: datetime | None = None


# --- Errors ---

class McpErrorData(BaseModel):
    error_type: str
    tool_id: str
    details: str
    duration_ms: float = 0


# --- JSON-RPC envelope ---

class JsonRpcRequest(BaseModel):
    jsonrpc: str = "2.0"
    id: str
    method: str
    params: dict[str, Any] = Field(default_factory=dict)


class JsonRpcSuccessResponse(BaseModel):
    jsonrpc: str = "2.0"
    id: str
    result: dict[str, Any]


class JsonRpcErrorResponse(BaseModel):
    jsonrpc: str = "2.0"
    id: str
    error: dict[str, Any]
```

---

## 6. Tool Manifest

Every tool adapter publishes a **manifest** describing its capabilities. The manifest is returned by `tool/list` and cached by the Tool Registry.

### Manifest Fields

| Field | Type | Description |
|-------|------|-------------|
| `tool_id` | `str` | Unique tool identifier: `<adapter_id>.<method_name>` |
| `adapter_id` | `str` | Adapter that provides this tool |
| `name` | `str` | Human-readable tool name |
| `description` | `str` | What the tool does |
| `capability` | `str` | Capability category (e.g., `"stress_analysis"`, `"erc_check"`) |
| `input_schema` | `dict` | JSON Schema for tool parameters |
| `output_schema` | `dict` | JSON Schema for tool results |
| `phase` | `int` | Minimum phase where this tool is available |
| `resource_limits` | `ResourceLimits` | CPU, memory, and disk limits for the container |

---

## 7. Tool Registry

The Tool Registry (`tool_registry/registry.py`) maintains a catalog of all available tools and their health status.

```python
class ToolRegistry:
    """Central catalog of available MCP tools with health tracking."""

    async def register_adapter(self, adapter_id: str, config: "AdapterConfig") -> None:
        """Register a tool adapter and discover its tools."""
        ...

    async def get_tool(self, tool_id: str) -> ToolManifest | None:
        """Look up a tool by ID."""
        ...

    async def list_tools(
        self,
        adapter_id: str | None = None,
        capability: str | None = None,
        phase: int | None = None,
    ) -> list[ToolManifest]:
        """Query available tools with optional filters."""
        ...

    async def health_check(self, adapter_id: str) -> HealthStatus:
        """Check adapter health and update internal status."""
        ...

    async def health_check_all(self) -> dict[str, HealthStatus]:
        """Check health of all registered adapters."""
        ...

    async def get_healthy_adapter(self, tool_id: str) -> str | None:
        """Get a healthy adapter that provides the given tool. Returns None if unavailable."""
        ...
```

### Adapter Configuration

```python
class AdapterConfig(BaseModel):
    adapter_id: str
    image: str  # Docker image name
    transport: str = "stdio"  # "stdio" or "http"
    host: str = "localhost"
    port: int = 8080
    workspace_mount: str = "/workspace"
    resource_limits: ResourceLimits = Field(default_factory=ResourceLimits)
    environment: dict[str, str] = Field(default_factory=dict)
    health_interval_seconds: int = 30
```

---

## 8. Execution Engine

The Execution Engine (`tool_registry/execution_engine.py`) manages the lifecycle of tool invocations.

### Invocation Lifecycle

```
Tool call received from MCP Client
        │
        ▼
  Resolve tool_id → adapter_id via Tool Registry
        │
        ▼
  Check adapter health
   ├── Unhealthy → return TOOL_UNAVAILABLE error
   └── Healthy → continue
        │
        ▼
  Start Docker container (if stdio) or reuse connection (if HTTP)
        │
        ▼
  Mount workspace directory (read-only or read-write per config)
        │
        ▼
  Send JSON-RPC request to container
        │
        ▼
  Wait for response (with timeout)
   ├── Timeout → TOOL_TIMEOUT error, kill container
   ├── Container crash → TOOL_EXECUTION_ERROR
   └── Success → parse and return result
        │
        ▼
  Record metrics (OpenTelemetry span + Prometheus counter)
        │
        ▼
  Cleanup container (if ephemeral)
        │
        ▼
  Return ToolCallResult to MCP Client
```

### Execution Engine Interface

```python
class ExecutionEngine:
    """Manages tool invocation lifecycle."""

    async def invoke(
        self,
        tool_id: str,
        arguments: dict,
        timeout_seconds: int = 120,
        trace_id: str | None = None,
    ) -> ToolCallResult:
        """
        Execute a tool call with full lifecycle management.

        1. Resolve adapter from registry.
        2. Start/connect to container.
        3. Send request, wait for result.
        4. Handle timeout, retry if idempotent.
        5. Record telemetry.
        6. Cleanup.
        """
        ...

    async def cancel(self, request_id: str) -> bool:
        """Cancel a running tool invocation."""
        ...
```

### Retry Policy

| Condition | Action |
|-----------|--------|
| Tool returns error, skill is `idempotent: true` | Retry up to `retries` times (from `definition.json`) |
| Tool returns error, skill is `idempotent: false` | No retry, propagate error |
| Timeout | Kill container, return TOOL_TIMEOUT error. Retry only if idempotent. |
| Container crash | Return TOOL_EXECUTION_ERROR. Retry only if idempotent. |
| Adapter unhealthy | Return TOOL_UNAVAILABLE immediately (no retry). |

---

## 9. Tool Adapter SDK

To create a new tool adapter, subclass `McpToolServer` and implement tool handlers.

### Step-by-Step Guide

**1. Create the adapter directory**:

```
tool_registry/tools/<adapter_name>/
├── server.py          # McpToolServer subclass
├── Dockerfile         # Container image definition
├── requirements.txt   # Python dependencies
└── tests/
    └── test_server.py
```

**2. Implement the server**:

```python
"""CalculiX FEA tool adapter."""

from tool_registry.mcp_server.base import McpToolServer, ToolManifest, ResourceLimits


class CalculixServer(McpToolServer):
    def __init__(self):
        super().__init__(adapter_id="calculix", version="0.1.0")
        self._register_tools()

    def _register_tools(self):
        self.register_tool(
            manifest=ToolManifest(
                tool_id="calculix.run_fea",
                adapter_id="calculix",
                name="Run FEA Analysis",
                description="Execute finite element analysis using CalculiX solver",
                capability="stress_analysis",
                input_schema={
                    "type": "object",
                    "properties": {
                        "mesh_file": {"type": "string", "description": "Path to .inp mesh file"},
                        "load_case": {"type": "string"},
                        "analysis_type": {"type": "string", "enum": ["static_stress", "thermal", "modal"]},
                    },
                    "required": ["mesh_file", "load_case", "analysis_type"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "max_von_mises": {"type": "object"},
                        "solver_time": {"type": "number"},
                        "mesh_elements": {"type": "integer"},
                    },
                },
                phase=1,
                resource_limits=ResourceLimits(max_memory_mb=2048, max_cpu_seconds=600, max_disk_mb=512),
            ),
            handler=self.run_fea,
        )

    async def run_fea(self, arguments: dict) -> dict:
        """Execute CalculiX FEA analysis."""
        import subprocess

        mesh_file = arguments["mesh_file"]
        analysis_type = arguments["analysis_type"]

        # Run CalculiX solver
        result = subprocess.run(
            ["ccx", "-i", mesh_file.replace(".inp", "")],
            capture_output=True,
            text=True,
            timeout=600,
        )

        if result.returncode != 0:
            raise RuntimeError(f"CalculiX failed: {result.stderr}")

        # Parse results from .frd output file
        # ... (parsing logic specific to CalculiX output format)

        return {
            "max_von_mises": parsed_stresses,
            "solver_time": elapsed,
            "mesh_elements": element_count,
        }


if __name__ == "__main__":
    import asyncio
    server = CalculixServer()
    asyncio.run(server.start_stdio())
```

**3. Write the Dockerfile**:

```dockerfile
FROM python:3.11-slim

# Install CalculiX
RUN apt-get update && apt-get install -y calculix-ccx && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .

# Workspace mount point
VOLUME ["/workspace"]

# No network access by default
# Network is controlled by Docker run flags

ENTRYPOINT ["python", "server.py"]
```

---

## 10. Phase 1 Adapters

### CalculiX Adapter (`tool_registry/tools/calculix/`)

| Property | Details |
|----------|---------|
| Docker Image | `ghcr.io/fidelodok/metaforge-calculix-adapter`, published by CI (FORGE-587) |
| Base Image | `python:3.11-slim` + `calculix-ccx` |
| Transport | stdio |
| Tools | `calculix.run_fea`, `calculix.run_thermal`, `calculix.validate_mesh` |

**Tools provided**:

| Tool ID | Capability | Description |
|---------|-----------|-------------|
| `calculix.run_fea` | `stress_analysis` | Static stress FEA using CalculiX solver |
| `calculix.run_thermal` | `thermal_analysis` | Thermal analysis (steady-state and transient) |
| `calculix.validate_mesh` | `mesh_validation` | Validate mesh quality (aspect ratio, element types) |

### CadQuery Adapter (`tool_registry/tools/cadquery/`)

| Property | Details |
|----------|---------|
| Docker Image | `ghcr.io/fidelodok/metaforge-cadquery-adapter`, published by CI (FORGE-593) |
| Base Image | `python:3.11-slim` (multi-stage) |
| Transport | stdio |
| Tools | `cadquery.create_parametric`, `cadquery.boolean_operation`, `cadquery.get_properties`, `cadquery.export_geometry`, `cadquery.execute_script`, `cadquery.create_assembly`, `cadquery.generate_enclosure` |

**Tools provided**:

| Tool ID | Capability | Description |
|---------|-----------|-------------|
| `cadquery.create_parametric` | `cad_generation` | Generate parametric CAD geometry from shape type and dimensions |
| `cadquery.boolean_operation` | `cad_operations` | CSG boolean operations (union, subtract, intersect) |
| `cadquery.get_properties` | `cad_analysis` | Geometric properties (volume, area, center of mass, bounding box, inertia) |
| `cadquery.export_geometry` | `cad_export` | Export to STEP/STL/OBJ/BREP/AMF/SVG |
| `cadquery.execute_script` | `cad_scripting` | Execute a sandboxed CadQuery Python script |
| `cadquery.create_assembly` | `cad_assembly` | Multi-part assembly from STEP files with constraints |
| `cadquery.generate_enclosure` | `cad_enclosure` | PCB enclosure from board dimensions and connector cutouts |

### FreeCAD Adapter (`tool_registry/tools/freecad/`)

| Property | Details |
|----------|---------|
| Docker Image | `ghcr.io/fidelodok/metaforge-freecad-adapter`, published by CI (see below) |
| Base Image | Debian system Python 3 (FreeCAD's compiled extension requires it, not a python.org image) |
| Transport | stdio |
| Tools | Stateful session API (`open_session`, `create_body`, `create_sketch`, `pad_sketch`, `execute_code`, `measure`, `export_model`, assembly joints, and more — 27 tools) plus a separate stateless legacy file-based surface (`export_geometry`, `generate_mesh`, `boolean_operation`, `get_properties`, `describe_step_file`, `create_parametric` — 6 tools) |

#### Adapter images are built in CI (FORGE-513)

The freecad and calculix adapters copy `mcp_core/`, `tool_registry/` and
`observability/` into their images, so a fix in those packages reaches an
adapter only after a rebuild. The `adapter-images` workflow builds both on
every push to `main` that touches those paths (and on manual dispatch) and
publishes `ghcr.io/fidelodok/metaforge-<freecad|calculix>-adapter:latest` and
`:<commit sha>`. `docker-compose.yml` names those images directly, so a dev
host pulls them:

```bash
docker compose pull freecad-adapter calculix-adapter
docker compose up -d --no-build freecad-adapter calculix-adapter
docker compose restart mcp-http
```

Building these images on the dev host itself has taken it offline, so prefer
the published ones. `METAFORGE_ADAPTER_TAG` pins a commit SHA instead of
`latest` when you need a specific build.

:::note[This used to need a manual retag]

Until FORGE-587 the compose file named `metaforge/<adapter>-adapter`, which
nothing published, so every host built locally — including the dev host whose
load this workflow exists to remove. The documented fix was to `docker tag` the
pulled image to the name compose expected. That step is gone: the names match
now, and `docker compose pull` is enough.

:::

Corrected from a stale table that listed `freecad.export_mesh`/`freecad.export_step`/`freecad.measure` — those tool ids don't exist in the current adapter. See [`capability-matrix.md`](capability-matrix.md#cad-kernel-capability-contract) for the 5-capability contract shared with CadQuery, and `tool_registry/tools/freecad/adapter.py` for the full tool list; it's large enough (33 tools across both surfaces) that duplicating it here would drift again.

#### Committing authored geometry: the stash is authoritative

`freecad.export_model` returns a multi-KB base64 STEP, and `twin.commit_geometry`
needs those bytes. Agents cannot reliably thread a value that large between two
tool calls, so both MCP dispatch seams — the unified sidecar and the in-process
registry bridge the gateway chat uses — keep a bounded LRU of each export's blob
keyed by `(session_id, obj_id)` (`skill_registry/geometry_stash.py`). A commit
that names those two ids gets the blob filled in server-side.

**When the stash has an entry for the named export, its copy is used even if the
call also carried a `step_base64`.** The stashed value came straight off the
adapter; anything carried back in is a copy of it, and a copy can only be equal
or wrong. This precedence used to be the other way round, which let a single
mistyped character in a 30,000-character base64 string silently replace a
pristine blob (MET-684): a committed STEP contained `NAMED_URIT(*)` where the
fixed OCCT boilerplate reads `NAMED_UNIT(*)`, and the OCCT converter crashed
parsing it. A divergence is repaired and logged as
`geometry_commit_blob_diverged`, never silently.

A stash **miss** leaves a supplied `step_base64` untouched — geometry produced
some other way (a raw CadQuery script, an upload) has no export to reference and
must still commit.

**Stateless tools** (`freecad.create_parametric`, `cadquery.create_parametric`/
`execute_script`/`generate_enclosure`, ...) have no `session_id`/`obj_id` at
all — they just return a `cad_file` path on the shared adapter workspace both
the gateway process and the adapter container mount. For these, pass that same
path as `twin.commit_geometry`'s `file_path` argument instead: the server reads
it directly off disk and base64-encodes it itself (FORGE-224), mirroring
`domain_agents.shared.commit_geometry.commit_geometry`'s own relative-path
resolution (against `ADAPTER_WORKSPACE_DIR`, default `/workspace`) that the
skill layer already used internally — this just makes the same ergonomics
available to a model calling `twin.commit_geometry` directly. `step_base64`
still wins when given alongside `file_path`, same precedence as the
session/obj_id path.

### Twin adapter: item keys and revision history (FORGE-523)

The definition write tools (`twin.commit_geometry`, `twin.record_constraint_set`,
`twin.record_engineering_entity` for `intent`/`stakeholder_need`/`objective`, and
`twin.record_component_selection`) write each call as the next revision of an
item (see [twin_schema.md section 2.30](twin_schema.md#230-items-and-revisions-definitions-vs-records-forge-522-forge-523)).
They accept three optional arguments, none of which the normal path needs.
Each is a string or `null`; `null` or an empty string means not given.

| Argument | Meaning |
|----------|---------|
| `item_key` | The item to revise, as an earlier result returned it (`CAD-BRACKET`). `KEY@n` also asserts the head is still revision `n`; a stale `@n` fails with nothing written. A bare key no item has yet names a new item. |
| `supersedes` | Node id of an earlier revision; the write revises that node's item. |
| `change_reason` | Why this revision differs from the previous one; stored on the `REVISION_OF` edge. |

With none given, the same name in the same project is the same item. Pass
`item_key` when a part keeps its identity under a new name. Every result
carries `item_key`, `revision` and `item_ref` (`KEY@n`). The revision's author
is the call context's `actor_id` and its `run_id` the context's design-flow run;
agents never number revisions themselves.

Inside a design-flow run (FORGE-525) a definition write is a **draft** in the
run's change set: its result adds `revision_status: "draft"` and a one-line
`draft_note`, and the item's head does not move until the run's gate approves.
The run's own reads see its drafts; other callers see approved heads only (see
[twin_schema.md, Drafts, approval and closed drafts](twin_schema.md#drafts-approval-and-closed-drafts-forge-525)).

`twin.item_history` (read-only, registered when the gateway or sidecar injects
`item_history_reader`) takes `item_key` or the `node_id` of any revision, plus an
optional `project_id` when a key exists in several projects, and returns:

```json
{
  "item": {"key": "CAD-LEG", "item_type": "cad_model", "name": "Leg, front left",
           "project_id": "...", "head_revision": 2, "head_node_id": "...",
           "head_ref": "CAD-LEG@2", "created_at": "...", "updated_at": "..."},
  "revisions": [
    {"revision": 1, "node_id": "...", "name": "Leg", "change_reason": null,
     "run_id": null, "author": "agent:mechanical", "is_head": false, "adopted": false},
    {"revision": 2, "node_id": "...", "name": "Leg, front left",
     "change_reason": "renamed and lengthened", "is_head": true, "adopted": false}
  ]
}
```

An unknown key, or a node that is not a revision of any item (a record, or an
unclassified type), is a tool error naming the reference.

FORGE-525 read semantics. Both reads follow the calling run (the call
context's `run_id`):

- `twin.item_history` lists the caller's own open drafts and never another
  run's; closed drafts are always listed. Every revision carries `status`
  (`committed`, `draft`, `approved`, `rejected`, `abandoned`), `change_set`,
  `phase`, `gate` and `status_reason`. The result adds `current`
  (`{"revision", "node_id", "ref"}`), what this caller reads as current: its
  run's draft if it has one, else the head. `item` adds `draft_revision`,
  `draft_node_id` and `draft_ref` when the caller's run has a draft; `head_*` is
  always the approved head (null for an item that only has drafts).
- `twin.get_node` accepts `item_key` instead of `node_id`: a bare `KEY` returns
  the node `current` names, `KEY@n` that revision (only if the caller can see
  it), with an optional `project_id` when the key exists in several projects.
  `node_id` is no longer required when `item_key` is given.

### Twin adapter: one home for requirements (FORGE-528)

Requirement values live only in the constraint set (`twin.record_constraint_set`).
No tool name or required argument changed; two tools behave differently (see
[twin_schema.md, One home for requirements](twin_schema.md#one-home-for-requirements-forge-528)).

**`twin.record_document` with `document_type='prd'`** records the text as the
prd's *prose* (background, scope, what is out of scope), as the next revision of
the project's one prd item (`PRD-...`; a new title does not start a second prd).
Nothing in the text is dropped and the write is never refused for its content.
Numbers written with a unit that no requirement in the current constraint set
states are reported. The result adds:

| Field | Meaning |
|-------|---------|
| `item_key`, `revision`, `item_ref` | The prd prose revision, as for every definition write. |
| `requirements_home` | One sentence: requirement values go in `twin.record_constraint_set`, and the prd is rendered from the prose plus the current constraint set (`GET /v1/twin/projects/{project_id}/prd`). |
| `requirement_refs` | The constraint set revisions the text was checked against (`["CS-WIDGET@2"]`). |
| `stray_requirement_values` | Only when there are some: the values as written (`["2 W", "$20"]`, at most 20). |
| `requirement_warning` | Only with stray values: names them and says to record them with `twin.record_constraint_set` if they are requirements. |

Every other `document_type` is unchanged.

**`twin.record_decision`** accepts an optional `depends_on`: an array of item
refs (`KEY@n`, or a bare `KEY` for the current revision; `null` means not given).
Each is pinned to an exact revision and linked with a `depends_on` edge; the
result echoes the pinned refs as `depends_on`. An unknown item or revision is a
tool error and nothing is written. Use it for a decision that rests on the
requirements: link `CS-...@n` instead of restating its values in the rationale.

### KiCad Adapter (`tool_registry/tools/kicad/`)

| Property | Details |
|----------|---------|
| Docker Image | `ghcr.io/fidelodok/metaforge-kicad-adapter`, published by CI (FORGE-593) |
| Base Image | `python:3.11-slim` (Debian trixie) + KiCad 9 (`kicad`, `kicad-symbols`, `kicad-footprints`), OpenJDK 25, Freerouting 2.5.0 (pinned, checksummed) |
| Transport | HTTP JSON-RPC on port 8103 (`POST /mcp`, `GET /health`); the gateway and sidecar reach it through `METAFORGE_ADAPTER_KICAD_URL` |
| Phase 1 Tools | Read-only: `kicad.run_erc`, `kicad.run_drc`, `kicad.export_bom`, `kicad.export_netlist`, `kicad.get_pin_mapping`, `kicad.export_gerber`, `kicad.export_drill`, `kicad.export_pos` |
| Phase 2 Tools | Write (a new project folder each, never the caller's files): `kicad.generate_schematic`, `kicad.generate_pcb`, `kicad.autoroute_pcb` (FORGE-683) |

**Phase 1 tools (read-only)**:

| Tool ID | Capability | Description |
|---------|-----------|-------------|
| `kicad.run_erc` | `erc_check` | Electrical rules check on schematic |
| `kicad.run_drc` | `drc_check` | Design rules check on PCB layout |
| `kicad.export_bom` | `bom_export` | Export BOM from schematic |
| `kicad.export_gerber` | `gerber_export` | Export Gerber manufacturing files |

**Phase 2 tools (write, FORGE-683)**:

| Tool ID | Capability | Description |
|---------|-----------|-------------|
| `kicad.generate_schematic` | `schematic_generation` | Circuit (components as KiCad symbol and footprint lib_ids, nets as `REF.PIN`) to a schematic of library symbols joined by global labels; then ERC and KiCad's exported netlist compared with the circuit. Returns `verdict`, `erc`, `netlist_check`, `power_flags`, `no_connects`, the file |
| `kicad.generate_pcb` | `pcb_generation` | Circuit with a `board` to a placed `.kicad_pcb` (pcbnew worker), pad nets from the schematic's netlist, footprints linked to symbols. Returns `placements`, `unplaced` |
| `kicad.autoroute_pcb` | `pcb_autoroute` | Freerouting over Specctra DSN/SES on a copy, ground pour, then DRC with schematic parity. Returns `unrouted`, `drc`, `schematic_parity`, tracks, vias; `pass` only when all are zero |

### SPICE Adapter (`tool_registry/tools/spice/`)

| Property | Details |
|----------|---------|
| Docker Image | `ghcr.io/fidelodok/metaforge-spice-adapter`, published by CI (FORGE-593; `tool_registry/tools/spice/Dockerfile`) |
| Base Image | `python:3.11-slim` + Debian `ngspice` |
| Transport | HTTP JSON-RPC on port 8104 (`POST /mcp`, `GET /health`); the gateway and sidecar reach it through `METAFORGE_ADAPTER_SPICE_URL` |
| Workspace | Shares `adapter-workspace` with the KiCad adapter, so a netlist from `kicad.export_netlist` is simulated by path |
| Tools | `spice.run_simulation` |

**Tools provided** (FORGE-542):

| Tool ID | Capability | Description |
|---------|-----------|-------------|
| `spice.run_simulation` | `circuit_simulation` | Run ngspice in batch mode on `netlist` text or a `netlist_path`, with one analysis card built from `analysis_type` (`op`, `dc`, `ac`, `transient`) and `params`. Parses ngspice's ASCII rawfile: per vector `final`/`min`/`max` (AC: magnitude in dB and phase), a decimated waveform keyed with the scale, `convergence`, and ngspice's errors in `log` when it fails |

---

### OpenFOAM Adapter (`tool_registry/tools/openfoam/`)

| Property | Details |
|----------|---------|
| Docker Image | `ghcr.io/fidelodok/metaforge-openfoam-adapter`, published by CI (FORGE-557; `tool_registry/tools/openfoam/Dockerfile`) |
| Base Image | `openfoam/openfoam11-paraview510` (OpenFOAM Foundation, Ubuntu 20.04), pinned by digest; Python 3.11 added with `uv` |
| Transport | HTTP JSON-RPC on port 8300 (`POST /mcp`, `GET /health`); the gateway and sidecar reach it through `METAFORGE_ADAPTER_OPENFOAM_URL` |
| Workspace | Shares `adapter-workspace` with the CAD adapters; surfaces are read by path and cases are written under `openfoam/<run id>/case` |
| Compose | Opt-in under the `cfd` profile; limited to 8 CPUs and 12 GB, one run at a time |
| Tools | `openfoam.run_internal_flow` |

**Tools provided** (FORGE-557):

| Tool ID | Capability | Description |
|---------|-----------|-------------|
| `openfoam.run_internal_flow` | `flow_simulation` | Steady incompressible internal flow through a domain bounded by `inlet_stl`, `outlet_stl` and `wall_stls` (units `stl_units`, a point `inside_point` in the fluid). Requires the `fluid` (density and kinematic viscosity), `inlet_velocity_m_s` and `turbulence` (`laminar`, or `kOmegaSST` with `turbulence_intensity` and `turbulence_length_scale_m`). Runs `blockMesh`, `snappyHexMesh`, `checkMesh` and `foamRun` (decomposed under `mpirun` when `solver.n_procs` is above 1). Returns `pressure_drop_pa`, inlet and outlet flow, mesh quality, residuals, `converged`, `solution_stable`, `y_plus` and warnings; a failed run returns `completed: false` with `stage_failed`, OpenFOAM's reason and a log tail. Invalid input is a JSON-RPC error. |

### Firmware Adapter (`tool_registry/tools/firmware/`)

| Property | Details |
|----------|---------|
| Docker Image | `ghcr.io/fidelodok/metaforge-firmware-adapter`, published by CI (FORGE-674/677; `tool_registry/tools/firmware/Dockerfile`) |
| Base Image | `python:3.11-slim`, with PlatformIO 6.2.0 and its ST STM32 20.0.0 (STM32Cube for F4, F7, H7) and Espressif 32 7.1.3 (ESP-IDF) platforms installed at image build by building seed projects, and cppcheck and clang-tidy by checking one; Renode 1.17.0 (portable); Espressif QEMU 9.2.2 (xtensa); FreeRTOS-Kernel V11.3.1 sources |
| Transport | HTTP JSON-RPC on port 8105 (`POST /mcp`, `GET /health`); the gateway and sidecar reach it through `METAFORGE_ADAPTER_FIRMWARE_URL` |
| Workspace | Shares `adapter-workspace`; a project is copied to `firmware/<run id>/project` and built there, and emulator runs write under `firmware/emulation/<run id>` |
| Compose | Opt-in under the `firmware` profile; limited to 4 CPUs and 4 GB, one build at a time |
| Network | None needed at run time: toolchains, frameworks and Renode's SVD files are in the image, board data is read from the platforms' manifests, and PlatformIO's upgrade check is pushed out (offline it otherwise waits a minute per call). A build needing a package that is not in the image is `not_established`, naming the package |
| Tools | `firmware.build`, `firmware.static_check`, `firmware.run_emulated` |

**Tools provided** (FORGE-674, FORGE-675, FORGE-677):

| Tool ID | Capability | Description |
|---------|-----------|-------------|
| `firmware.build` | `firmware_build` | Compile `project_path` (a PlatformIO project) or `files` with `board` and `framework` (`stm32cube`, `espidf`). Adds the FreeRTOS kernel to an STM32 project carrying a `FreeRTOSConfig.h`. Returns `verdict` (`pass`, `fail`, `not_established` on timeout), `error_count`, `warning_count`, `diagnostics` (file, line, column, severity, message), `flash` and `ram` (`used_bytes`, `total_bytes`, `used_pct`), `toolchain`, `elf_path`, `log_path`, and `log_tail` on failure. A board outside the installed platforms is a JSON-RPC error. |
| `firmware.static_check` | `firmware_static_analysis` | `pio check` on the same project input as `firmware.build`, with `tools` (`cppcheck` default, `clangtidy`), `fail_on` (`high` default) and `suppressions` (`{id, file?, reason}`, cppcheck). Returns `verdict`, `passed`, `findings` (tool, severity, category, id, cwe, file, line, column, message), `counts` per severity, `runs` (each checker and whether it completed) and `log_path`. |
| `firmware.run_emulated` | `firmware_emulation` | Run `elf_path` for `board` for `sim_time_s` emulated seconds on Renode (STM32F4-Discovery, Nucleo-F767ZI, Nucleo-H753ZI) or Espressif QEMU (ESP32), and check `uart_expect` (in order), `uart_forbid` and, on Renode, `memory_expect`. Returns `verdict`, the `emulator` and its version, each assertion with its status, and the UART log. An unmodelled board is `not_established` with the list of modelled boards. |

---

## 11. Container Isolation Model

All tool adapters run in Docker containers with strict security controls.

### Docker Run Configuration

```python
CONTAINER_CONFIG = {
    "network_mode": "none",           # No external network access
    "read_only": True,                # Read-only root filesystem
    "tmpfs": {"/tmp": "size=256m"},   # Writable temp directory
    "mem_limit": "2g",                # Memory limit
    "cpu_period": 100000,             # CPU throttling
    "cpu_quota": 200000,              # 2 CPU cores max
    "pids_limit": 100,                # Process limit
    "security_opt": ["no-new-privileges:true"],
}
```

### Volume Mounts

| Mount | Container Path | Mode | Purpose |
|-------|---------------|------|---------|
| Project workspace | `/workspace` | `ro` (default) or `rw` (Phase 2 write tools) | Design files |
| Tool output | `/output` | `rw` | Tool results and generated files |
| Temp | `/tmp` | `rw` (tmpfs) | Scratch space for solver intermediates |

### Lifecycle

1. **Create**: Container is created from the adapter's Docker image.
2. **Mount**: Workspace and output volumes are mounted.
3. **Execute**: JSON-RPC request is sent via stdin, response read from stdout.
4. **Collect**: Output files are collected from `/output`.
5. **Destroy**: Container is removed after the tool call completes.

For frequently-used tools, a **warm pool** of pre-started containers can be maintained (configured per adapter). Warm containers are reused for subsequent calls but still have the same isolation properties.

---

## 12. Error Taxonomy

All MCP errors use standard JSON-RPC 2.0 error codes plus MetaForge-specific application codes.

| Code | Name | Description |
|------|------|-------------|
| `-32600` | `INVALID_REQUEST` | Malformed JSON-RPC request |
| `-32601` | `METHOD_NOT_FOUND` | Unknown method (e.g., `tool/call` with invalid tool_id) |
| `-32602` | `INVALID_PARAMS` | Tool arguments fail schema validation |
| `-32001` | `TOOL_EXECUTION_ERROR` | Tool ran but produced an error (solver crash, invalid input). **Legacy `tool/call` only** — on `tools/call` this is an `isError` result instead (§4.4.1) |
| `-32002` | `RESOURCE_NOT_FOUND` | No such resource. **The code the MCP spec assigns**, so a spec-aware client reads it this way whatever we intend |
| `-32003` | `TOOL_UNAVAILABLE` | Tool adapter is unhealthy or not registered |
| `-32005` | `RESOURCE_READ_ERROR` | The resource exists and could not be read |
| `-32006` | `TOOL_TIMEOUT` | Tool exceeded its timeout limit |
| `-32007` | `AUTH_DENIED` | A credential was rejected |

These live in one place, `mcp_core/protocol.py`, and every module imports
them. FORGE-388 found **three** tables that disagreed: `-32002` meant
`TOOL_TIMEOUT` in one, auth-denied in another, and the adapters emitted
`-32004` for a missing resource — while the spec reserves `-32002` for
exactly that. A client branching on `-32002` could not tell a missing
resource from a timeout from a rejected credential, which is the whole
purpose of a numeric code.

`TOOL_TIMEOUT` and `AUTH_DENIED` moved to free codes rather than the
spec-assigned one. Both are MetaForge-specific, so no client outside this
repo was reading them.

### Error Response Format

```python
class McpError(Exception):
    """Base exception for MCP protocol errors."""

    def __init__(self, code: int, message: str, data: McpErrorData | None = None):
        self.code = code
        self.message = message
        self.data = data
        super().__init__(message)


class ToolExecutionError(McpError):
    def __init__(self, tool_id: str, details: str, duration_ms: float = 0):
        super().__init__(
            code=-32001,
            message="Tool execution failed",
            data=McpErrorData(
                error_type="TOOL_EXECUTION_ERROR",
                tool_id=tool_id,
                details=details,
                duration_ms=duration_ms,
            ),
        )


class ToolTimeoutError(McpError):
    def __init__(self, tool_id: str, timeout_seconds: int):
        super().__init__(
            code=-32006,
            message=f"Tool exceeded timeout of {timeout_seconds}s",
            data=McpErrorData(
                error_type="TOOL_TIMEOUT",
                tool_id=tool_id,
                details=f"Execution exceeded {timeout_seconds} second limit",
            ),
        )


class ToolUnavailableError(McpError):
    def __init__(self, tool_id: str):
        super().__init__(
            code=-32003,
            message="Tool adapter is unavailable",
            data=McpErrorData(
                error_type="TOOL_UNAVAILABLE",
                tool_id=tool_id,
                details="Adapter is unhealthy or not registered",
            ),
        )
```

### Session edits persist; tool results (FORGE-512)

A session is one live FreeCAD document, so an edit made in one call is visible to the next.

- `freecad.transform_object` now returns the verified `placement` (`position` in mm, `rotation` as `axis` plus `angle_deg`) and the measured `volume_mm3`, `surface_area_mm2` and global `bounding_box`. It raises instead of reporting success if the placement did not stick.
- `freecad.measure`, `describe_model` and the bounding box above are measured in the global frame (own placement plus parent `App::Part` placements), so moving an assembly container is visible when a child is measured, and the reverse.
- `freecad.execute_code` returns the script's `result` variable as plain data under `result`: Vectors, Placements, bounding boxes and document objects are converted, anything else falls back to `repr`, and the value is capped at 20000 serialized characters (`result_truncated: true` when cut). When `result` is a document object it is still registered and returned as `obj_id`.
- A script that assigns `obj.Shape` on a parametric object (for example a `Part::Box` primitive) used to lose the edit to the recompute that follows. The new shape is now pinned, and survives later `transform_object` calls.

### Mesh coordinate frame (FORGE-505)

`freecad.generate_mesh` returns a mesh in the Digital Twin's coordinate frame: node coordinates equal the committed work product's `bounding_box` axes. If the STEP carries a Placement as an assembly transform (a part authored on a rotated plane), which gmsh's reader drops, the placement is baked into the geometry before meshing (`placement_baked: true`). The result also carries `surface_sets` (named surface set to `bbox_mm`, `centroid_mm`, `area_mm2`, `normal`), `mesh_bbox_mm` and `coordinate_frame: "twin"`, so a caller picks fixed and load faces by position. `calculix.run_fea`'s `load_force_n` is expressed in this same frame.

`freecad.export_model` keeps the stored STEP in the same frame the twin records (FORGE-505). `Import.export` stores an object's Placement (from `transform_object`, or a sketch on a rotated plane) as an assembly transform that gmsh and `Part.Shape.read` drop, so the stored file's own axes used to differ from the committed `bounding_box` (cycled axes). Export now checks the file read placement-blind against the live placed bounding box and, on a mismatch, re-exports with placements baked into the geometry (Labels kept). The `volume_mm3`, `surface_area_mm2` and `bounding_box` it returns are measured from the exported bytes. The mesh-time bake above still covers STEPs stored before this fix.

`freecad.export_model` can write part colours into the STEP (FORGE-517). Headless FreeCAD has no `ViewObject`, so `Import.export` writes no colours and parts rendered uniform grey, while the viewer takes colours only from the model's STEP (MET-537: no render-time palette). Pass `material` (a recorded material string such as `18 mm birch plywood` or `PETG`), `color` (`[r, g, b]`, 0-1 or 0-255) or `part_materials` (an assembly part Label mapped to its material, for per-part colours). The colour is authored into the final STEP bytes as `COLOUR_RGB` / `STYLED_ITEM` entities, after the FORGE-505 placement bake, so the bake's re-export cannot drop it. An explicit `color` wins over `material`; an unknown material (or none) leaves the part uncoloured, since no colour is invented. The table lives in `tool_registry/tools/freecad/materials_appearance.py` (case-insensitive whole-word match, first row wins, so `birch plywood` is matched before `plywood`) and is meant to move into the materials library (FORGE-445). The STEP-to-GLB converter keeps these colours (it reads them through XCAF), and the viewer renders the GLB's own materials. The colour chain is STEP `COLOUR_RGB` (sRGB, the table value) to GLB `baseColorFactor` (linear, as glTF requires; OCCT returns linear, so 0.87 is stored as about 0.73, which is correct and not a darkening) to renderer output (sRGB). The viewer pins `outputColorSpace` to sRGB and uses Neutral tone mapping (`dashboard/src/components/viewer/viewerColor.ts`) instead of ACES Filmic, which had been darkening and desaturating flat part colours, so a 0.87 table value displays as about 0.87. A converter test (`tools/occt-converter/test_convert.py`, `TestColourChain`) and a viewer test pin the chain.

`freecad.import_step` keeps each part's STEP colour (FORGE-519). Headless `Import.insert` keeps geometry and Labels but drops colours, so a coloured part imported and exported again used to come out grey. The import reads the source file's colours per PRODUCT name (`read_step_colours` in `materials_appearance.py`, following `STYLED_ITEM` through the presentation style chain to `COLOUR_RGB`), records each component's colour on its session object (`metadata.color`, `metadata.color_source: imported_step`), and returns it per part as `parts[].color` (`[r, g, b]` 0-1, or `null` when the file has none). A component matches its product by Label (FreeCAD's `001` de-duplication suffix allowed); a single-component import whose file has exactly one colour takes that colour. `freecad.export_model` then uses the recorded colour as the lowest-priority source: `part_materials` first, then `color` / `material`, then the imported colour. A single imported part exports in its own colour, and an assembly built from imported parts gives each part (matched by its live Label) its recorded colour, so no colour arguments are needed to keep them.

`freecad.export_model` also returns `stored_properties` (`volume_mm3`, `surface_area_mm2`, `center_of_mass`, `bounding_box`), measured from the exact STEP it wrote. The geometry stash threads it into `twin.commit_geometry`, which records it in preference to the session object's measurement (`bbox_mm`, `volume_mm3`, `surface_area_mm2`, `center_of_mass_mm`). If the session's bounding box differs from the stored one by more than 0.01 mm, a `commit_geometry_session_stored_bbox_diverge` warning is logged and the session values are kept as `metadata.session_properties`.
