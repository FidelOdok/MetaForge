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

### FreeCAD Adapter (`tool_registry/tools/freecad/`)

| Property | Details |
|----------|---------|
| Docker Image | `metaforge/adapter-freecad:0.1` |
| Base Image | `python:3.11-slim` + FreeCAD headless |
| Transport | stdio |
| Tools | `freecad.export_mesh`, `freecad.export_step`, `freecad.measure` |

**Tools provided**:

| Tool ID | Capability | Description |
|---------|-----------|-------------|
| `freecad.export_mesh` | `mesh_generation` | Generate FEA-ready mesh from CAD geometry |
| `freecad.export_step` | `cad_export` | Export to STEP/STL/IGES formats |
| `freecad.measure` | `geometry_measurement` | Measure distances, angles, volumes |

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
| `-32001` | `TOOL_EXECUTION_ERROR` | Tool ran but produced an error (solver crash, invalid input) |
| `-32002` | `TOOL_TIMEOUT` | Tool exceeded its timeout limit |
| `-32003` | `TOOL_UNAVAILABLE` | Tool adapter is unhealthy or not registered |

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
            code=-32002,
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
