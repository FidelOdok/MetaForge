"""Concrete McpBridge that delegates to an McpClient for real tool invocation.

MET-306 hardens the bridge for production:

* explicit ``asyncio.wait_for`` deadline so a hung tool can't block the
  caller forever (raises ``McpToolError`` with ``error_type=timeout``);
* small retry budget on transient transport failures (connection-reset
  / broken-pipe / ``ToolUnavailableError``) with exponential back-off
  capped at 30 s — long-running gateway calls survive a server reboot;
* permanent failures (``ToolExecutionError`` from the server) propagate
  on the first attempt — retrying a deterministic tool error doesn't
  help and can mask bugs.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

from mcp_core.client import McpClient
from mcp_core.protocol import (
    ToolExecutionError,
    ToolUnavailableError,
)
from mcp_core.schemas import ToolCallRequest
from skill_registry.mcp_bridge import McpBridge, McpToolError

logger = structlog.get_logger(__name__)

DEFAULT_TIMEOUT_SECONDS = 120
DEFAULT_RETRIES = 2
DEFAULT_BACKOFF_INITIAL_SECONDS = 0.5
DEFAULT_BACKOFF_CAP_SECONDS = 30.0


class McpClientBridge(McpBridge):
    """Bridge from McpBridge (skill/agent interface) to McpClient (protocol layer).

    Wraps an already-connected ``McpClient``. Connection setup (transport
    creation, ``client.connect``, ``tool/list`` discovery) is the
    factory's job — see ``skill_registry.bridge_factory``.
    """

    def __init__(
        self,
        client: McpClient,
        *,
        default_timeout: int = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = DEFAULT_RETRIES,
        backoff_initial: float = DEFAULT_BACKOFF_INITIAL_SECONDS,
        backoff_cap: float = DEFAULT_BACKOFF_CAP_SECONDS,
    ) -> None:
        self._client = client
        self._default_timeout = default_timeout
        self._max_retries = max_retries
        self._backoff_initial = backoff_initial
        self._backoff_cap = backoff_cap

    async def invoke(
        self,
        tool_id: str,
        params: dict[str, Any],
        timeout: int | None = None,
    ) -> dict[str, Any]:
        """Invoke a tool through the MCP client with timeout + retry."""
        deadline = timeout or self._default_timeout
        request = ToolCallRequest(
            tool_id=tool_id,
            arguments=params,
            timeout_seconds=deadline,
        )

        attempt = 0
        backoff = self._backoff_initial
        last_transport_error: Exception | None = None

        while True:
            try:
                result = await asyncio.wait_for(
                    self._client.call_tool(request),
                    timeout=deadline,
                )
            except TimeoutError as exc:
                logger.warning(
                    "mcp_bridge_timeout",
                    tool_id=tool_id,
                    timeout_seconds=deadline,
                )
                raise McpToolError(
                    tool_id,
                    f"Tool call timed out after {deadline}s",
                ) from exc
            except ToolExecutionError as exc:
                # Server-side execution error — deterministic, don't retry.
                # Extract the inner ``details`` carried in the structured
                # ``McpErrorData`` payload, falling back to the generic
                # outer message when absent.
                payload = getattr(exc, "data", None)
                details = getattr(payload, "details", None) or str(exc)
                raise McpToolError(tool_id, details) from exc
            except (ToolUnavailableError, ConnectionError, BrokenPipeError) as exc:
                # Transport-level failure — eligible for retry.
                last_transport_error = exc
                attempt += 1
                if attempt > self._max_retries:
                    logger.error(
                        "mcp_bridge_transport_exhausted",
                        tool_id=tool_id,
                        attempts=attempt,
                        error=str(exc),
                    )
                    raise McpToolError(
                        tool_id,
                        f"Transport unavailable after {attempt} attempts: {exc}",
                    ) from exc
                logger.warning(
                    "mcp_bridge_transport_retry",
                    tool_id=tool_id,
                    attempt=attempt,
                    backoff_seconds=backoff,
                    error=str(exc),
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self._backoff_cap)
                continue
            except Exception as exc:  # noqa: BLE001 — defensive top-level guard
                raise McpToolError(tool_id, str(exc)) from exc

            if result.status != "success":
                raise McpToolError(
                    tool_id,
                    f"Tool returned status: {result.status}",
                )
            if last_transport_error is not None:
                logger.info(
                    "mcp_bridge_recovered",
                    tool_id=tool_id,
                    attempts=attempt + 1,
                )
            return result.data

    async def is_available(self, tool_id: str) -> bool:
        """Check if a tool is registered in the MCP client."""
        try:
            tools = await self._client.list_tools()
        except Exception as exc:  # noqa: BLE001 — best-effort discovery
            logger.warning("mcp_bridge_list_tools_failed", error=str(exc))
            return False
        return any(t.tool_id == tool_id for t in tools)

    async def list_tools(self, capability: str | None = None) -> list[dict[str, Any]]:
        """List available tools from the MCP client."""
        manifests = await self._client.list_tools()
        result = [m.model_dump() for m in manifests]
        if capability is not None:
            result = [t for t in result if t.get("capability") == capability]
        return result
