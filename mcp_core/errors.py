"""Standardized MCP tool error contract (MET-385).

Today every MCP tool can return errors in its own ad-hoc shape, so
the harness has to write per-tool error handling. This module
defines the single envelope every MetaForge tool error reports
through, plus the canonical error-code enum the harness branches on.

Layer-1 invariant: this module imports only stdlib + pydantic. No
upstream tool / transport / agent imports. Tools and transports
import down from here; nothing here reaches up.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ErrorCode(StrEnum):
    """Canonical MCP tool error codes.

    The harness branches on these — keep the set small and stable.
    Adding a new code is a contract change every harness has to absorb;
    prefer extending ``McpToolError.details`` for tool-specific data.
    """

    # User-fixable (the harness should surface to the human / agent).
    INVALID_INPUT = "invalid_input"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    CONSTRAINT_VIOLATION = "constraint_violation"
    AUTH_REQUIRED = "auth_required"
    PERMISSION_DENIED = "permission_denied"

    # Transient (harness may retry with backoff).
    BACKEND_UNAVAILABLE = "backend_unavailable"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"

    # Catch-all (treat as non-retryable; bug or unexpected condition).
    INTERNAL = "internal"


# Codes the harness can usefully retry on. Single source of truth so
# every adapter and the transport-level wrapper agree.
RETRYABLE_CODES: frozenset[ErrorCode] = frozenset(
    {
        ErrorCode.BACKEND_UNAVAILABLE,
        ErrorCode.TIMEOUT,
        ErrorCode.RATE_LIMITED,
    }
)


class McpToolError(BaseModel):
    """The envelope every MetaForge MCP tool error rides in.

    The wire format is a JSON-RPC error response with this dict
    serialised into ``error.data``::

        {
            "jsonrpc": "2.0",
            "id": "<request_id>",
            "error": {
                "code": -32001,           # JSON-RPC tool-execution code
                "message": "<short>",
                "data": {                 # this McpToolError serialised
                    "code": "invalid_input",
                    "message": "...",
                    "details": {...},
                    "retryable": false,
                    "trace_id": "..."
                }
            }
        }

    ``trace_id`` is populated when OTel instrumentation lands (MET-386).
    Until then it stays ``None`` — the field is reserved so the contract
    doesn't change shape later.
    """

    code: ErrorCode = Field(description="Canonical error class. Harness branches on this.")
    message: str = Field(description="Human-readable summary. Safe to surface to UI.")
    details: dict[str, Any] | None = Field(
        default=None,
        description="Tool-specific payload — field-level errors, conflict pointers, etc.",
    )
    retryable: bool = Field(
        description=(
            "True only when the harness can usefully retry. Derive from ``code`` "
            "via ``RETRYABLE_CODES`` unless the tool has a specific reason to override."
        ),
    )
    trace_id: str | UUID | None = Field(
        default=None,
        description="OTel trace id when MET-386 instrumentation is wired. None until then.",
    )

    model_config = ConfigDict(frozen=True)


def make_tool_error(
    code: ErrorCode,
    message: str,
    *,
    details: dict[str, Any] | None = None,
    retryable: bool | None = None,
    trace_id: str | UUID | None = None,
) -> McpToolError:
    """Construct a ``McpToolError`` with sensible defaults.

    ``retryable`` falls back to ``code in RETRYABLE_CODES`` so callers
    only have to override when they know better than the default
    classification.
    """
    if retryable is None:
        retryable = code in RETRYABLE_CODES
    return McpToolError(
        code=code,
        message=message,
        details=details,
        retryable=retryable,
        trace_id=trace_id,
    )


__all__ = [
    "RETRYABLE_CODES",
    "ErrorCode",
    "McpToolError",
    "make_tool_error",
]
