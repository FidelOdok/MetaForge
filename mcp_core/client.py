"""MCP client for tool communication."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from uuid import uuid4

import structlog

from mcp_core.protocol import (
    McpError,
    ToolExecutionError,
    ToolUnavailableError,
    create_request,
    deserialize_response,
    serialize_message,
)
from mcp_core.schemas import (
    HealthStatus,
    JsonRpcErrorResponse,
    ToolCallRequest,
    ToolCallResult,
    ToolManifest,
)

logger = structlog.get_logger()


class Transport(ABC):
    """Abstract transport layer for MCP communication."""

    @abstractmethod
    async def send(self, message: str) -> str:
        """Send a message and return the response."""
        ...

    @abstractmethod
    async def connect(self) -> None:
        """Establish the transport connection."""
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """Close the transport connection."""
        ...

    @abstractmethod
    def is_connected(self) -> bool:
        """Check if transport is connected."""
        ...


class InMemoryTransport(Transport):
    """In-memory transport for testing.

    Stores requests and returns pre-configured responses.
    """

    def __init__(self) -> None:
        self._connected = False
        self._responses: list[str] = []
        self._requests: list[str] = []

    def queue_response(self, response: str) -> None:
        """Queue a response to be returned on next send()."""
        self._responses.append(response)

    @property
    def requests(self) -> list[str]:
        """Return the list of sent requests."""
        return self._requests

    async def send(self, message: str) -> str:
        """Send a message and return the next queued response."""
        self._requests.append(message)
        if not self._responses:
            raise McpError(-32001, "No response queued in InMemoryTransport")
        return self._responses.pop(0)

    async def connect(self) -> None:
        """Mark transport as connected."""
        self._connected = True

    async def disconnect(self) -> None:
        """Mark transport as disconnected."""
        self._connected = False

    def is_connected(self) -> bool:
        """Check if transport is connected."""
        return self._connected


class McpClient:
    """Manages connections to MCP tool servers and dispatches tool calls."""

    def __init__(self) -> None:
        self._transports: dict[str, Transport] = {}
        self._manifests: dict[str, ToolManifest] = {}
        self._adapter_for_tool: dict[str, str] = {}

    async def connect(self, adapter_id: str, transport: Transport) -> None:
        """Establish connection to a tool adapter server."""
        await transport.connect()
        self._transports[adapter_id] = transport
        logger.info("Connected to adapter", adapter_id=adapter_id)

    async def disconnect(self, adapter_id: str) -> None:
        """Close connection to a tool adapter server."""
        transport = self._transports.pop(adapter_id, None)
        if transport is not None:
            await transport.disconnect()
            logger.info("Disconnected from adapter", adapter_id=adapter_id)

    async def call_tool(self, request: ToolCallRequest) -> ToolCallResult:
        """Send a tool/call request and wait for the result."""
        adapter_id = self._adapter_for_tool.get(request.tool_id)
        if adapter_id is None:
            raise ToolUnavailableError(request.tool_id)

        transport = self._transports.get(adapter_id)
        if transport is None or not transport.is_connected():
            raise ToolUnavailableError(request.tool_id)

        rpc_request = create_request(
            method="tool/call",
            params={
                "tool_id": request.tool_id,
                "arguments": request.arguments,
                "timeout_seconds": request.timeout_seconds,
                "trace_id": request.trace_id or str(uuid4()),
            },
        )

        start = time.monotonic()
        raw_response = await transport.send(serialize_message(rpc_request))
        elapsed_ms = (time.monotonic() - start) * 1000

        response = deserialize_response(raw_response)
        if isinstance(response, JsonRpcErrorResponse):
            error_data = response.error
            raise ToolExecutionError(
                tool_id=request.tool_id,
                details=error_data.get("message", "Unknown error"),
                duration_ms=elapsed_ms,
            )

        result_data = response.result
        return ToolCallResult(
            tool_id=request.tool_id,
            status=result_data.get("status", "success"),
            data=result_data.get("data", {}),
            duration_ms=elapsed_ms,
            output_files=result_data.get("output_files", []),
        )

    async def list_tools(self, adapter_id: str | None = None) -> list[ToolManifest]:
        """Discover available tools from one or all connected adapters."""
        if adapter_id is not None:
            return [m for m in self._manifests.values() if m.adapter_id == adapter_id]
        return list(self._manifests.values())

    def register_manifest(self, manifest: ToolManifest) -> None:
        """Register a tool manifest.

        Typically called after connecting to an adapter.
        """
        self._manifests[manifest.tool_id] = manifest
        self._adapter_for_tool[manifest.tool_id] = manifest.adapter_id

    async def health_check(self, adapter_id: str) -> HealthStatus:
        """Check the health of a specific adapter."""
        transport = self._transports.get(adapter_id)
        if transport is None or not transport.is_connected():
            return HealthStatus(
                adapter_id=adapter_id,
                status="unhealthy",
                version="unknown",
                tools_available=0,
                uptime_seconds=0,
            )

        rpc_request = create_request(method="health/check")
        raw_response = await transport.send(serialize_message(rpc_request))
        response = deserialize_response(raw_response)

        if isinstance(response, JsonRpcErrorResponse):
            return HealthStatus(
                adapter_id=adapter_id,
                status="unhealthy",
                version="unknown",
                tools_available=0,
                uptime_seconds=0,
            )

        return HealthStatus.model_validate(response.result)
