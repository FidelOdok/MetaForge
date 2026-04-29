"""Base class for MCP tool servers (tool adapters)."""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime
from typing import Any

import structlog

from observability.tracing import get_tracer
from tool_registry.mcp_server.handlers import (
    ResourceManifestEntry,
    ResourceNotFoundError,
    ResourceReader,
    ResourceReadError,
    ResourceRegistration,
    ToolHandler,
    ToolHandlerError,
    ToolManifest,
    ToolNotFoundError,
    ToolRegistration,
    handle_health_check,
    handle_resources_list,
    handle_resources_read,
    handle_tool_call,
    handle_tool_list,
    make_error,
    make_success,
)

logger = structlog.get_logger()
tracer = get_tracer("tool_registry.mcp_server.server")

# JSON-RPC error codes
_INVALID_REQUEST = -32600
_METHOD_NOT_FOUND = -32601
_TOOL_EXECUTION_ERROR = -32001
_RESOURCE_NOT_FOUND = -32004
_RESOURCE_READ_ERROR = -32005


class McpToolServer:
    """Base class for MCP tool adapter servers.

    Subclass this to create a new tool adapter. Implement:
    1. Register tools in __init__ using register_tool()
    2. Define async handler methods for each tool

    Example::

        class CalculixServer(McpToolServer):
            def __init__(self):
                super().__init__(adapter_id="calculix", version="0.1.0")
                self.register_tool(manifest=..., handler=self.run_fea)

            async def run_fea(self, arguments: dict) -> dict:
                ...
    """

    def __init__(self, adapter_id: str, version: str) -> None:
        self.adapter_id = adapter_id
        self.version = version
        self._tools: dict[str, ToolRegistration] = {}
        self._resources: dict[str, ResourceRegistration] = {}
        self._start_time = datetime.now(UTC)

    def register_tool(self, manifest: ToolManifest, handler: ToolHandler) -> None:
        """Register a tool with its manifest and handler function."""
        if manifest.tool_id in self._tools:
            raise ValueError(f"Tool '{manifest.tool_id}' is already registered")
        self._tools[manifest.tool_id] = ToolRegistration(manifest, handler)
        logger.info("Registered tool", tool_id=manifest.tool_id, adapter=self.adapter_id)

    def register_resource(
        self,
        manifest: ResourceManifestEntry,
        reader: ResourceReader,
        matcher: Any,
    ) -> None:
        """Register a discoverable read-only resource (MET-384).

        ``matcher(uri) -> bool`` decides whether the registration
        owns a given concrete URI. Use a closure that pattern-matches
        the suffix after ``metaforge://<adapter>/`` — keeping the
        matcher inside the adapter avoids baking templating into the
        server.
        """
        key = manifest.uri_template
        if key in self._resources:
            raise ValueError(f"Resource '{key}' is already registered")
        self._resources[key] = ResourceRegistration(manifest, reader, matcher)
        logger.info(
            "Registered resource",
            uri_template=manifest.uri_template,
            adapter=self.adapter_id,
        )

    @property
    def tool_ids(self) -> list[str]:
        """List registered tool IDs."""
        return list(self._tools.keys())

    @property
    def resource_uri_templates(self) -> list[str]:
        """List the registered resource URI templates."""
        return list(self._resources.keys())

    async def handle_request(self, raw_message: str) -> str:
        """Parse JSON-RPC request, dispatch to handler, return JSON-RPC response.

        MET-386: every request is wrapped in an ``mcp.tool.call`` span
        so harness latency questions ("this took 4s — where?") have a
        trace tree to read. Per-tool execution adds an inner
        ``mcp.tool.<tool_id>`` span via ``handle_tool_call``.
        """
        # Parse the request
        try:
            data: dict[str, Any] = json.loads(raw_message)
        except json.JSONDecodeError:
            response = make_error("null", _INVALID_REQUEST, "Invalid JSON")
            return json.dumps(response)

        request_id: str = data.get("id", "null")
        method: str = data.get("method", "")
        params: dict[str, Any] = data.get("params", {})

        if data.get("jsonrpc") != "2.0":
            response = make_error(request_id, _INVALID_REQUEST, "Not a valid JSON-RPC 2.0 message")
            return json.dumps(response)

        # MET-386: open the root MCP span. Attributes follow the
        # ``mcp.*`` namespace convention so backend filters in Tempo /
        # Jaeger are easy. Pull caller identity from MET-387's call
        # context when available.
        from mcp_core.context import current_context

        ctx = current_context()
        with tracer.start_as_current_span("mcp.tool.call") as span:
            span.set_attribute("mcp.method", method)
            span.set_attribute("mcp.adapter_id", self.adapter_id)
            span.set_attribute("mcp.adapter_version", self.version)
            span.set_attribute("mcp.actor_id", ctx.actor_id)
            span.set_attribute("mcp.session_id", str(ctx.session_id))
            if ctx.project_id is not None:
                span.set_attribute("mcp.project_id", str(ctx.project_id))
            tool_id = params.get("tool_id") if method == "tool/call" else None
            if tool_id:
                span.set_attribute("mcp.tool_id", tool_id)
            # Best-effort body-size attribute for "is this query big?" debug.
            span.set_attribute("mcp.request_size_bytes", len(raw_message))

            try:
                if method == "tool/list":
                    result = await handle_tool_list(self._tools, params)
                elif method == "tool/call":
                    result = await handle_tool_call(self._tools, params)
                elif method == "resources/list":
                    result = await handle_resources_list(self._resources, params)
                elif method == "resources/read":
                    result = await handle_resources_read(self._resources, params)
                elif method == "health/check":
                    result = await handle_health_check(
                        self.adapter_id, self.version, self._tools, self._start_time
                    )
                else:
                    span.set_attribute("mcp.status", "method_not_found")
                    response = make_error(
                        request_id, _METHOD_NOT_FOUND, f"Unknown method: {method}"
                    )
                    return json.dumps(response)
            except ResourceNotFoundError as exc:
                span.set_attribute("mcp.status", "resource_not_found")
                span.set_attribute("mcp.error.uri", exc.uri)
                span.record_exception(exc)
                response = make_error(
                    request_id,
                    _RESOURCE_NOT_FOUND,
                    str(exc),
                    {"uri": exc.uri},
                )
                return json.dumps(response)
            except ResourceReadError as exc:
                span.set_attribute("mcp.status", "resource_read_error")
                span.set_attribute("mcp.error.uri", exc.uri)
                span.record_exception(exc)
                logger.error("Resource read failed", uri=exc.uri, details=exc.details)
                response = make_error(
                    request_id,
                    _RESOURCE_READ_ERROR,
                    "Resource read failed",
                    {"uri": exc.uri, "details": exc.details},
                )
                return json.dumps(response)
            except ToolNotFoundError as exc:
                span.set_attribute("mcp.status", "tool_not_found")
                span.set_attribute("mcp.error.tool_id", exc.tool_id)
                span.record_exception(exc)
                response = make_error(
                    request_id, _METHOD_NOT_FOUND, str(exc), {"tool_id": exc.tool_id}
                )
                return json.dumps(response)
            except ToolHandlerError as exc:
                span.set_attribute("mcp.status", "tool_execution_error")
                span.set_attribute("mcp.error.tool_id", exc.tool_id)
                span.set_attribute("mcp.duration_ms", round(exc.duration_ms, 2))
                span.record_exception(exc)
                logger.error(
                    "Tool handler failed",
                    tool_id=exc.tool_id,
                    details=exc.details,
                    duration_ms=round(exc.duration_ms, 2),
                )
                response = make_error(
                    request_id,
                    _TOOL_EXECUTION_ERROR,
                    "Tool execution failed",
                    {
                        "error_type": "TOOL_EXECUTION_ERROR",
                        "tool_id": exc.tool_id,
                        "details": exc.details,
                        "duration_ms": exc.duration_ms,
                    },
                )
                return json.dumps(response)

            span.set_attribute("mcp.status", "success")
            response_text = json.dumps(make_success(request_id, result))
            span.set_attribute("mcp.response_size_bytes", len(response_text))
            return response_text

    async def start_stdio(self) -> None:
        """Start the server in stdio mode (reads stdin, writes responses to stdout)."""
        logger.info("Starting MCP server (stdio)", adapter=self.adapter_id)
        loop = asyncio.get_event_loop()
        reader = asyncio.StreamReader()
        transport, _ = await loop.connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(reader), sys.stdin
        )

        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                raw = line.decode("utf-8").strip()
                if not raw:
                    continue
                response_str = await self.handle_request(raw)
                sys.stdout.write(response_str + "\n")
                sys.stdout.flush()
        except asyncio.CancelledError:
            pass
        finally:
            transport.close()
            logger.info("MCP server (stdio) stopped", adapter=self.adapter_id)
