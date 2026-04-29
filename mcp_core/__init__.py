"""MCP core -- Model Context Protocol client layer for MetaForge."""

from mcp_core.client import InMemoryTransport, McpClient, Transport
from mcp_core.context import (
    McpCallContext,
    context_from_env,
    context_from_headers,
    current_context,
    reset_context,
    set_context,
    with_context,
)
from mcp_core.errors import (
    RETRYABLE_CODES,
    ErrorCode,
    McpToolError,
    make_tool_error,
)
from mcp_core.protocol import (
    McpError,
    ToolExecutionError,
    ToolTimeoutError,
    ToolUnavailableError,
)
from mcp_core.resources import (
    SCHEME,
    ParsedResourceUri,
    ResourceUriError,
    parse_resource_uri,
)
from mcp_core.schemas import (
    HealthStatus,
    JsonRpcErrorResponse,
    JsonRpcRequest,
    JsonRpcSuccessResponse,
    ResourceContent,
    ResourceListRequest,
    ResourceListResult,
    ResourceManifest,
    ResourceReadRequest,
    ResourceReadResult,
    ToolCallRequest,
    ToolCallResult,
    ToolManifest,
)
from mcp_core.transports import LoopbackTransport
from mcp_core.versioning import (
    DEFAULT_VERSION,
    deprecation_message,
    normalise_version,
    parse_versioned_tool_id,
    versioned_tool_id,
)

__all__ = [
    "DEFAULT_VERSION",
    "RETRYABLE_CODES",
    "SCHEME",
    "ErrorCode",
    "HealthStatus",
    "InMemoryTransport",
    "JsonRpcErrorResponse",
    "JsonRpcRequest",
    "JsonRpcSuccessResponse",
    "LoopbackTransport",
    "McpCallContext",
    "McpClient",
    "McpError",
    "McpToolError",
    "ParsedResourceUri",
    "ResourceContent",
    "ResourceListRequest",
    "ResourceListResult",
    "ResourceManifest",
    "ResourceReadRequest",
    "ResourceReadResult",
    "ResourceUriError",
    "ToolCallRequest",
    "ToolCallResult",
    "ToolExecutionError",
    "ToolManifest",
    "ToolTimeoutError",
    "ToolUnavailableError",
    "Transport",
    "context_from_env",
    "context_from_headers",
    "current_context",
    "deprecation_message",
    "make_tool_error",
    "normalise_version",
    "parse_resource_uri",
    "parse_versioned_tool_id",
    "reset_context",
    "set_context",
    "versioned_tool_id",
    "with_context",
]
