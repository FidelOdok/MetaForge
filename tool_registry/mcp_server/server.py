"""Base class for MCP tool servers (tool adapters)."""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime
from typing import Any

import structlog

from tool_registry.mcp_server.handlers import (
    ToolHandler,
    ToolHandlerError,
    ToolManifest,
    ToolNotFoundError,
    ToolRegistration,
    handle_health_check,
    handle_tool_call,
    handle_tool_list,
    make_error,
    make_success,
)

logger = structlog.get_logger()

# JSON-RPC error codes
_INVALID_REQUEST = -32600
_METHOD_NOT_FOUND = -32601
_TOOL_EXECUTION_ERROR = -32001


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
        self._start_time = datetime.now(UTC)

    def register_tool(self, manifest: ToolManifest, handler: ToolHandler) -> None:
        """Register a tool with its manifest and handler function."""
        if manifest.tool_id in self._tools:
            raise ValueError(f"Tool '{manifest.tool_id}' is already registered")
        self._tools[manifest.tool_id] = ToolRegistration(manifest, handler)
        logger.info("Registered tool", tool_id=manifest.tool_id, adapter=self.adapter_id)

    @property
    def tool_ids(self) -> list[str]:
        """List registered tool IDs."""
        return list(self._tools.keys())

    async def handle_request(self, raw_message: str) -> str:
        """Parse JSON-RPC request, dispatch to handler, return JSON-RPC response."""
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

        # Route to handler
        try:
            if method == "tool/list":
                result = await handle_tool_list(self._tools, params)
            elif method == "tool/call":
                result = await handle_tool_call(self._tools, params)
            elif method == "health/check":
                result = await handle_health_check(
                    self.adapter_id, self.version, self._tools, self._start_time
                )
            else:
                response = make_error(request_id, _METHOD_NOT_FOUND, f"Unknown method: {method}")
                return json.dumps(response)
        except ToolNotFoundError as exc:
            response = make_error(request_id, _METHOD_NOT_FOUND, str(exc), {"tool_id": exc.tool_id})
            return json.dumps(response)
        except ToolHandlerError as exc:
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

        response = make_success(request_id, result)
        return json.dumps(response)

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
