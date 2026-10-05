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
the session declared `elicitation` at `initialize` on revision
`2025-06-18` or later, has no `GET /mcp` stream open, and sent the call
with `text/event-stream` in `Accept`. The stream carries the
`elicitation/create` request, then the tool result, then closes. A call
that is not held keeps its JSON response, so clients that never open
`GET /mcp` (Claude Code among them) can still be asked inline. Routing
rules and outcomes are in
[Over HTTP: which stream carries the question](capability-matrix.md#over-http-which-stream-carries-the-question).

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
| Docker Image | `metaforge/adapter-calculix:0.1` |
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
| Docker Image | `metaforge/cadquery-adapter` |
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
| Docker Image | `metaforge/freecad-adapter` locally; published by CI as `ghcr.io/fidelodok/metaforge-freecad-adapter` (see below) |
| Base Image | Debian system Python 3 (FreeCAD's compiled extension requires it, not a python.org image) |
| Transport | stdio |
| Tools | Stateful session API (`open_session`, `create_body`, `create_sketch`, `pad_sketch`, `execute_code`, `measure`, `export_model`, assembly joints, and more — 27 tools) plus a separate stateless legacy file-based surface (`export_geometry`, `generate_mesh`, `boolean_operation`, `get_properties`, `describe_step_file`, `create_parametric` — 6 tools) |

#### Adapter images are built in CI (FORGE-513)

The freecad and calculix adapters copy `mcp_core/`, `tool_registry/` and
`observability/` into their images, so a fix in those packages reaches an
adapter only after a rebuild. The `adapter-images` workflow builds both on
every push to `main` that touches those paths (and on manual dispatch) and
publishes `ghcr.io/fidelodok/metaforge-<freecad|calculix>-adapter:latest` and
`:<commit sha>`. To deploy on a dev host without building there:

```bash
docker pull ghcr.io/fidelodok/metaforge-freecad-adapter:latest
docker tag ghcr.io/fidelodok/metaforge-freecad-adapter:latest metaforge/freecad-adapter:latest
docker compose up -d --no-build freecad-adapter
docker compose restart mcp-http
```

(the same for `calculix`). Building these images on the dev host itself has
taken it offline, so prefer the published images.

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

### KiCad Adapter (`tool_registry/tools/kicad/`)

| Property | Details |
|----------|---------|
| Docker Image | `metaforge/adapter-kicad:0.1` |
| Base Image | `python:3.11-slim` + KiCad CLI |
| Transport | stdio |
| Phase 1 Tools | Read-only: `kicad.run_erc`, `kicad.run_drc`, `kicad.export_bom`, `kicad.export_gerber` |
| Phase 2 Tools | Write: `kicad.generate_schematic`, `kicad.auto_route` |

**Phase 1 tools (read-only)**:

| Tool ID | Capability | Description |
|---------|-----------|-------------|
| `kicad.run_erc` | `erc_check` | Electrical rules check on schematic |
| `kicad.run_drc` | `drc_check` | Design rules check on PCB layout |
| `kicad.export_bom` | `bom_export` | Export BOM from schematic |
| `kicad.export_gerber` | `gerber_export` | Export Gerber manufacturing files |

### SPICE Adapter (`tool_registry/tools/spice/`)

| Property | Details |
|----------|---------|
| Docker Image | `metaforge/adapter-spice:0.1` |
| Base Image | `python:3.11-slim` + ngspice |
| Transport | stdio |
| Tools | `spice.simulate` |

**Tools provided**:

| Tool ID | Capability | Description |
|---------|-----------|-------------|
| `spice.simulate` | `circuit_simulation` | Run SPICE simulation (DC, AC, transient analysis) |

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
