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


# --- Resources (MET-384) -----------------------------------------------------


class ResourceManifestEntry(BaseModel):
    """Server-side manifest for a discoverable resource.

    The wire shape mirrors ``mcp_core.schemas.ResourceManifest`` but
    lives here too so handlers don't need a layer-2→layer-1 import.
    """

    uri_template: str
    name: str
    description: str
    mime_type: str = "application/json"
    adapter_id: str


# A reader takes a concrete URI and returns one or more ``ResourceContent``-shaped
# dicts (``uri``, ``mime_type``, ``text``/``blob_base64``).
ResourceReader = Callable[[str], Awaitable[list[dict[str, Any]]]]


class ResourceRegistration:
    """Internal record of a registered resource manifest + reader."""

    def __init__(
        self,
        manifest: ResourceManifestEntry,
        reader: ResourceReader,
        matcher: Callable[[str], bool],
    ) -> None:
        self.manifest = manifest
        self.reader = reader
        # ``matcher`` decides whether a concrete URI belongs to this
        # registration. We don't bake URI templating into the server —
        # adapters know best how to recognise their own URIs.
        self.matcher = matcher


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
    """Handle tool/call request -- dispatch to the registered handler.

    MET-386: opens a per-tool ``mcp.tool.<tool_id>`` span as a child of
    the enclosing ``mcp.tool.call`` root, so trace trees show
    per-handler latency. Backend spans (Neo4j, pgvector, ...) inherit
    from this one when adapters use their own tracers.
    """
    from observability.tracing import get_tracer

    tracer = get_tracer("tool_registry.mcp_server.handlers")

    tool_id = params.get("tool_id", "")
    arguments = params.get("arguments", {})

    registration = tools.get(tool_id)
    if registration is None:
        raise ToolNotFoundError(tool_id)

    span_name = f"mcp.tool.{tool_id}" if tool_id else "mcp.tool.unknown"
    with tracer.start_as_current_span(span_name) as span:
        span.set_attribute("mcp.tool_id", tool_id)
        span.set_attribute("mcp.tool.adapter_id", registration.manifest.adapter_id)
        span.set_attribute("mcp.tool.capability", registration.manifest.capability)
        # Best-effort: argument count is a useful "is this a big call?"
        # signal without serialising potentially-huge payloads.
        if isinstance(arguments, dict):
            span.set_attribute("mcp.tool.argument_count", len(arguments))

        start = time.monotonic()
        try:
            result_data = await registration.handler(arguments)
        except ToolNotFoundError:
            raise
        except Exception as exc:
            elapsed = (time.monotonic() - start) * 1000
            span.set_attribute("mcp.tool.duration_ms", round(elapsed, 2))
            span.record_exception(exc)
            raise ToolHandlerError(tool_id, str(exc), elapsed) from exc

        elapsed = (time.monotonic() - start) * 1000
        span.set_attribute("mcp.tool.duration_ms", round(elapsed, 2))
        if isinstance(result_data, dict):
            span.set_attribute("mcp.tool.result_keys", len(result_data))
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


class ResourceNotFoundError(Exception):
    """Raised when ``resources/read`` URI matches no registration."""

    def __init__(self, uri: str) -> None:
        self.uri = uri
        super().__init__(f"Resource not found: {uri}")


class ResourceReadError(Exception):
    """Raised when a registered resource reader fails."""

    def __init__(self, uri: str, details: str) -> None:
        self.uri = uri
        self.details = details
        super().__init__(f"Resource read failed for {uri}: {details}")


# --- Resource handlers (MET-384) ---------------------------------------------


async def handle_resources_list(
    resources: dict[str, ResourceRegistration],
    params: dict[str, Any],
) -> dict[str, Any]:
    """Return the discoverable manifest for every registered resource.

    Optional ``adapter_id`` filter narrows to one adapter — useful when
    a federated server wraps several adapters and the harness only
    cares about one.
    """
    adapter_filter = params.get("adapter_id")
    out: list[dict[str, Any]] = []
    for reg in resources.values():
        if adapter_filter is None or reg.manifest.adapter_id == adapter_filter:
            out.append(reg.manifest.model_dump())
    return {"resources": out}


async def handle_resources_read(
    resources: dict[str, ResourceRegistration],
    params: dict[str, Any],
) -> dict[str, Any]:
    """Dispatch ``resources/read`` to the matching registration."""
    uri = params.get("uri")
    if not isinstance(uri, str) or not uri:
        raise ResourceReadError(str(uri), "uri is required")

    for reg in resources.values():
        if reg.matcher(uri):
            try:
                contents = await reg.reader(uri)
            except ResourceNotFoundError:
                raise
            except Exception as exc:
                raise ResourceReadError(uri, str(exc)) from exc
            return {"contents": list(contents)}

    raise ResourceNotFoundError(uri)
