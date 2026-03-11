"""MCP core -- Model Context Protocol client layer for MetaForge."""

from mcp_core.client import InMemoryTransport, McpClient, Transport
from mcp_core.protocol import (
    McpError,
    ToolExecutionError,
    ToolTimeoutError,
    ToolUnavailableError,
)
from mcp_core.schemas import (
    HealthStatus,
    JsonRpcErrorResponse,
    JsonRpcRequest,
    JsonRpcSuccessResponse,
    ToolCallRequest,
    ToolCallResult,
    ToolManifest,
)
from mcp_core.transports import LoopbackTransport

__all__ = [
    "HealthStatus",
    "InMemoryTransport",
    "LoopbackTransport",
    "JsonRpcErrorResponse",
    "JsonRpcRequest",
    "JsonRpcSuccessResponse",
    "McpClient",
    "McpError",
    "ToolCallRequest",
    "ToolCallResult",
    "ToolExecutionError",
    "ToolManifest",
    "ToolTimeoutError",
    "ToolUnavailableError",
    "Transport",
]
