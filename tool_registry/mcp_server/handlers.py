"""Request handlers for MCP tool server methods."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

# --- Manifest and config types (local to tool_registry to avoid circular deps) ---


class ResourceLimits(BaseModel):
    """Resource limits for a tool adapter container."""

    max_memory_mb: int = 1024
    max_cpu_seconds: int = 300
    max_disk_mb: int = 256


class ToolManifest(BaseModel):
    """Manifest describing a single tool's capabilities."""

    tool_id: str
    adapter_id: str
    name: str
    description: str
    capability: str
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    phase: int = 1
    resource_limits: ResourceLimits = Field(default_factory=ResourceLimits)


# Type alias for tool handler functions
ToolHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class ToolRegistration:
    """Internal record of a registered tool with its handler."""

    def __init__(self, manifest: ToolManifest, handler: ToolHandler) -> None:
        self.manifest = manifest
        self.handler = handler


# --- JSON-RPC 2.0 helpers (self-contained to avoid dep on mcp_core) ---


def make_success(request_id: str, result: dict[str, Any]) -> dict[str, Any]:
    """Create a JSON-RPC success response dict."""
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def make_error(
    request_id: str,
    code: int,
    message: str,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a JSON-RPC error response dict."""
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


async def handle_tool_list(
    tools: dict[str, ToolRegistration],
    params: dict[str, Any],
) -> dict[str, Any]:
    """Handle tool/list request -- return manifests of registered tools."""
    capability_filter = params.get("capability")
    manifests = []
    for reg in tools.values():
        if capability_filter is None or reg.manifest.capability == capability_filter:
            manifests.append(reg.manifest.model_dump())
    return {"tools": manifests}


async def handle_tool_call(
    tools: dict[str, ToolRegistration],
    params: dict[str, Any],
) -> dict[str, Any]:
    """Handle tool/call request -- dispatch to the registered handler."""
    tool_id = params.get("tool_id", "")
    arguments = params.get("arguments", {})

    registration = tools.get(tool_id)
    if registration is None:
        raise ToolNotFoundError(tool_id)

    start = time.monotonic()
    try:
        result_data = await registration.handler(arguments)
    except ToolNotFoundError:
        raise
    except Exception as exc:
        elapsed = (time.monotonic() - start) * 1000
        raise ToolHandlerError(tool_id, str(exc), elapsed) from exc

    elapsed = (time.monotonic() - start) * 1000
    return {
        "tool_id": tool_id,
        "status": "success",
        "data": result_data,
        "duration_ms": round(elapsed, 2),
    }


async def handle_health_check(
    adapter_id: str,
    version: str,
    tools: dict[str, ToolRegistration],
    start_time: datetime,
) -> dict[str, Any]:
    """Handle health/check request."""
    now = datetime.now(UTC)
    uptime = (now - start_time).total_seconds()
    return {
        "adapter_id": adapter_id,
        "status": "healthy",
        "version": version,
        "tools_available": len(tools),
        "uptime_seconds": round(uptime, 1),
    }


class ToolNotFoundError(Exception):
    """Raised when a tool/call references an unknown tool_id."""

    def __init__(self, tool_id: str) -> None:
        self.tool_id = tool_id
        super().__init__(f"Tool not found: {tool_id}")


class ToolHandlerError(Exception):
    """Raised when a tool handler raises during execution."""

    def __init__(self, tool_id: str, details: str, duration_ms: float = 0) -> None:
        self.tool_id = tool_id
        self.details = details
        self.duration_ms = duration_ms
        super().__init__(f"Tool '{tool_id}' handler failed: {details}")
