"""``UnifiedMcpServer`` — aggregates every adapter into one MCP process (MET-337).

Each adapter under ``tool_registry/tools/`` already extends
``McpToolServer`` with its own ``register_tool(...)`` calls and tool
handlers. This module composes them: one process holds the full set,
serves a single ``tool/list`` (across every adapter), and routes
``tool/call`` to the right handler by tool-id prefix
(``knowledge.*`` → ``KnowledgeServer``, ``cadquery.*`` →
``CadqueryServer``, etc.).

The class is transport-agnostic — feed it raw JSON-RPC text and get
back JSON-RPC text. ``__main__.py`` wraps it with a stdio reader/writer
loop or a FastAPI HTTP/SSE app.

Why we don't subclass ``McpToolServer``: that class carries a single
``adapter_id`` and version, and its ``health/check`` reports per-adapter
state. The unified server roll-up needs different shapes for both, so
composition (this class holds a list of adapter servers) is cleaner
than inheritance.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import structlog

from observability.tracing import get_tracer
from tool_registry.bootstrap import bootstrap_tool_registry
from tool_registry.mcp_server.handlers import (
    ToolHandlerError,
    ToolNotFoundError,
    make_error,
    make_success,
)
from tool_registry.mcp_server.server import McpToolServer
from tool_registry.registry import ToolRegistry

logger = structlog.get_logger(__name__)
tracer = get_tracer("metaforge.mcp.server")


# JSON-RPC error codes (mirrors ``tool_registry.mcp_server.server`` so
# clients see consistent codes regardless of which entry point they hit).
_INVALID_REQUEST = -32600
_METHOD_NOT_FOUND = -32601
_TOOL_EXECUTION_ERROR = -32001
# MET-338: dedicated code for failed API-key auth so clients can branch
# on it without parsing message strings.
_AUTH_DENIED = -32002


class UnifiedMcpServer:
    """Holds a set of ``McpToolServer`` adapters and dispatches across them.

    Construction is decoupled from boot: build the server with already-
    initialised adapters (see ``build_unified_server``). That keeps unit
    tests fast — they can supply lightweight stub adapters without
    touching the real tool_registry bootstrap path.
    """

    def __init__(
        self,
        adapters: list[McpToolServer],
        version: str = "0.1.0",
    ) -> None:
        self._adapters = list(adapters)
        self._version = version
        self._start_time = datetime.now(UTC)
        # tool_id → adapter (built once at construction; tool sets are
        # static after each adapter's ``__init__``).
        self._tool_index: dict[str, McpToolServer] = {}
        for adapter in self._adapters:
            for tool_id in adapter.tool_ids:
                if tool_id in self._tool_index:
                    raise ValueError(
                        f"Tool id collision: {tool_id!r} registered by "
                        f"both {self._tool_index[tool_id].adapter_id!r} "
                        f"and {adapter.adapter_id!r}"
                    )
                self._tool_index[tool_id] = adapter
        logger.info(
            "unified_mcp_initialised",
            adapter_count=len(self._adapters),
            tool_count=len(self._tool_index),
            adapter_ids=[a.adapter_id for a in self._adapters],
        )

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def adapters(self) -> list[McpToolServer]:
        return list(self._adapters)

    @property
    def tool_ids(self) -> list[str]:
        return list(self._tool_index)

    # ------------------------------------------------------------------
    # JSON-RPC entry point
    # ------------------------------------------------------------------

    async def handle_request(self, raw_message: str) -> str:
        """Parse JSON-RPC request, dispatch, return JSON-RPC response.

        Same contract as ``McpToolServer.handle_request``: pure
        text-in / text-out so transports can wrap it freely.
        """
        try:
            data: dict[str, Any] = json.loads(raw_message)
        except json.JSONDecodeError:
            return json.dumps(make_error("null", _INVALID_REQUEST, "Invalid JSON"))

        request_id: str = data.get("id", "null")
        method: str = data.get("method", "")
        params: dict[str, Any] = data.get("params", {})

        if data.get("jsonrpc") != "2.0":
            return json.dumps(
                make_error(request_id, _INVALID_REQUEST, "Not a valid JSON-RPC 2.0 message")
            )

        with tracer.start_as_current_span("unified_mcp.handle_request") as span:
            span.set_attribute("rpc.method", method)
            try:
                if method == "tool/list":
                    result = await self._tool_list(params)
                elif method == "tool/call":
                    result = await self._tool_call(params)
                elif method == "health/check":
                    result = await self._health_check()
                else:
                    return json.dumps(
                        make_error(request_id, _METHOD_NOT_FOUND, f"Unknown method: {method}")
                    )
            except ToolNotFoundError as exc:
                return json.dumps(
                    make_error(
                        request_id,
                        _METHOD_NOT_FOUND,
                        str(exc),
                        {"tool_id": exc.tool_id},
                    )
                )
            except ToolHandlerError as exc:
                logger.error(
                    "unified_mcp_tool_failed",
                    tool_id=exc.tool_id,
                    duration_ms=round(exc.duration_ms, 2),
                    details=exc.details,
                )
                return json.dumps(
                    make_error(
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
                )

            return json.dumps(make_success(request_id, result))

    # ------------------------------------------------------------------
    # Method handlers
    # ------------------------------------------------------------------

    async def _tool_list(self, params: dict[str, Any]) -> dict[str, Any]:
        """Aggregate ``tool/list`` across every registered adapter.

        Honours the ``capability`` filter the per-adapter handler
        already supports.
        """
        capability = params.get("capability")
        manifests: list[dict[str, Any]] = []
        for adapter in self._adapters:
            for reg in adapter._tools.values():  # noqa: SLF001 — adapter is sibling
                if capability is None or reg.manifest.capability == capability:
                    manifests.append(reg.manifest.model_dump())
        return {"tools": manifests}

    async def _tool_call(self, params: dict[str, Any]) -> dict[str, Any]:
        """Route ``tool/call`` to the adapter that owns ``tool_id``."""
        tool_id = params.get("tool_id", "")
        adapter = self._tool_index.get(tool_id)
        if adapter is None:
            raise ToolNotFoundError(tool_id)

        # Delegate to the adapter's own JSON-RPC dispatcher so its
        # per-tool error handling, timing, and structlog records all
        # apply unchanged.
        sub_request = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": "unified",
                "method": "tool/call",
                "params": params,
            }
        )
        sub_response_text = await adapter.handle_request(sub_request)
        sub_response: dict[str, Any] = json.loads(sub_response_text)

        if "error" in sub_response:
            err = sub_response["error"]
            data = err.get("data") or {}
            tid = data.get("tool_id", tool_id)
            if err["code"] == _METHOD_NOT_FOUND:
                raise ToolNotFoundError(tid)
            raise ToolHandlerError(
                tid,
                data.get("details") or err.get("message", "Tool execution failed"),
                float(data.get("duration_ms", 0.0)),
            )
        return sub_response.get("result", {})

    async def _health_check(self) -> dict[str, Any]:
        """Aggregate health across every adapter into one report."""
        now = datetime.now(UTC)
        uptime = (now - self._start_time).total_seconds()
        adapter_health: list[dict[str, Any]] = []
        for adapter in self._adapters:
            adapter_health.append(
                {
                    "adapter_id": adapter.adapter_id,
                    "version": adapter.version,
                    "tools_available": len(adapter.tool_ids),
                }
            )
        return {
            "service": "metaforge-mcp",
            "version": self._version,
            "status": "healthy",
            "uptime_seconds": round(uptime, 1),
            "adapter_count": len(self._adapters),
            "tool_count": len(self._tool_index),
            "adapters": adapter_health,
        }


# ---------------------------------------------------------------------------
# Bootstrap helper
# ---------------------------------------------------------------------------


async def build_unified_server(
    adapter_ids: list[str] | None = None,
    knowledge_service: Any = None,
) -> UnifiedMcpServer:
    """Discover and instantiate every enabled adapter, then wrap.

    Reuses ``tool_registry.bootstrap.bootstrap_tool_registry`` so the
    unified server picks up the same env-driven adapter allow-list
    (``METAFORGE_ADAPTERS``, ``METAFORGE_ADAPTER_<ID>_ENABLED``) as the
    main gateway. Knowledge adapter is included only when a
    ``KnowledgeService`` instance is supplied (matches the gateway
    contract from MET-335).
    """
    registry: ToolRegistry = await bootstrap_tool_registry(
        adapter_ids=adapter_ids,
        knowledge_service=knowledge_service,
    )
    return UnifiedMcpServer(adapters=registry.list_adapter_servers())
