"""MCP wire protocol -- JSON-RPC 2.0 message handling."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from mcp_core.schemas import (
    JsonRpcErrorResponse,
    JsonRpcRequest,
    JsonRpcSuccessResponse,
    McpErrorData,
)

# --- Error codes (JSON-RPC 2.0 standard + MCP-specific) ---

# JSON-RPC 2.0 reserves -32000..-32099 for implementation-defined server
# errors, and the MCP spec pins two of them: -32002 is "Resource not
# found". FORGE-388: this table had -32002 as TOOL_TIMEOUT and
# RESOURCE_NOT_FOUND at -32004, while `metaforge/mcp/server.py` kept a
# *second* table where -32002 meant "auth denied" -- three meanings for
# one number, none of them the spec's. A client branching on -32002 could
# not tell a missing resource from a timeout from a rejected credential,
# which is the entire purpose of a numeric code.
#
# One table now. Spec-assigned codes match the spec; the rest keep the
# values they already had wherever nothing forced a move.
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
TOOL_EXECUTION_ERROR = -32001
#: Per the MCP spec. Moved here from -32004.
RESOURCE_NOT_FOUND = -32002
TOOL_UNAVAILABLE = -32003
RESOURCE_READ_ERROR = -32005
#: Moved off -32002, which the spec had already claimed.
TOOL_TIMEOUT = -32006
#: A credential was rejected. Previously -32002 in a second table.
AUTH_DENIED = -32007


class McpError(Exception):
    """Base exception for MCP protocol errors."""

    def __init__(self, code: int, message: str, data: McpErrorData | None = None) -> None:
        self.code = code
        self.message = message
        self.data = data
        super().__init__(message)


class ToolExecutionError(McpError):
    """Tool ran but produced an error.

    MET-579: ``details`` must be part of the exception *message*, not only
    ``.data``. Every layer above (RegistryMcpBridge, the harness tool loop,
    the agent's observation, the eval step trace) surfaces ``str(exc)`` —
    with a bare "Tool execution failed" the model couldn't see WHY a call
    failed (e.g. "project already exists") and diagnosis took log
    archaeology instead of one glance at the step trace.
    """

    def __init__(self, tool_id: str, details: str, duration_ms: float = 0) -> None:
        super().__init__(
            code=TOOL_EXECUTION_ERROR,
            message=f"Tool execution failed: {details}" if details else "Tool execution failed",
            data=McpErrorData(
                error_type="TOOL_EXECUTION_ERROR",
                tool_id=tool_id,
                details=details,
                duration_ms=duration_ms,
            ),
        )


class ToolTimeoutError(McpError):
    """Tool exceeded its timeout."""

    def __init__(self, tool_id: str, timeout_seconds: int) -> None:
        super().__init__(
            code=TOOL_TIMEOUT,
            message=f"Tool exceeded timeout of {timeout_seconds}s",
            data=McpErrorData(
                error_type="TOOL_TIMEOUT",
                tool_id=tool_id,
                details=f"Exceeded {timeout_seconds}s limit",
            ),
        )


class ToolUnavailableError(McpError):
    """Tool adapter is unhealthy or not registered."""

    def __init__(self, tool_id: str) -> None:
        super().__init__(
            code=TOOL_UNAVAILABLE,
            message="Tool adapter is unavailable",
            data=McpErrorData(
                error_type="TOOL_UNAVAILABLE",
                tool_id=tool_id,
                details="Adapter is unhealthy or not registered",
            ),
        )


def create_request(
    method: str,
    params: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> JsonRpcRequest:
    """Create a JSON-RPC request."""
    return JsonRpcRequest(
        id=request_id or str(uuid4()),
        method=method,
        params=params or {},
    )


def create_success_response(request_id: str, result: dict[str, Any]) -> JsonRpcSuccessResponse:
    """Create a JSON-RPC success response."""
    return JsonRpcSuccessResponse(id=request_id, result=result)


def create_error_response(
    request_id: str,
    code: int,
    message: str,
    data: dict[str, Any] | None = None,
) -> JsonRpcErrorResponse:
    """Create a JSON-RPC error response."""
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return JsonRpcErrorResponse(id=request_id, error=error)


def serialize_message(
    message: JsonRpcRequest | JsonRpcSuccessResponse | JsonRpcErrorResponse,
) -> str:
    """Serialize a JSON-RPC message to a JSON string."""
    return message.model_dump_json()


def deserialize_request(raw: str) -> JsonRpcRequest:
    """Deserialize a JSON string to a JsonRpcRequest.

    Raises McpError on invalid input.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise McpError(INVALID_REQUEST, f"Invalid JSON: {exc}") from exc

    if not isinstance(data, dict) or data.get("jsonrpc") != "2.0":
        raise McpError(INVALID_REQUEST, "Not a valid JSON-RPC 2.0 message")

    return JsonRpcRequest.model_validate(data)


def deserialize_response(
    raw: str,
) -> JsonRpcSuccessResponse | JsonRpcErrorResponse:
    """Deserialize a JSON string to a response.

    Returns either a success or error variant.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise McpError(INVALID_REQUEST, f"Invalid JSON: {exc}") from exc

    if "error" in data:
        return JsonRpcErrorResponse.model_validate(data)
    return JsonRpcSuccessResponse.model_validate(data)
