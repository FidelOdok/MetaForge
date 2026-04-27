"""Bridge factory — selects the right ``McpBridge`` at gateway boot (MET-306).

Three modes today, picked by env var ``METAFORGE_MCP_BRIDGE``:

* ``registry`` (default) — in-process bridge over the local
  ``ToolRegistry`` (every adapter loaded by ``bootstrap_tool_registry``).
  This is the historical gateway behaviour.
* ``http`` — connect to an external MCP server over HTTP. Requires
  ``METAFORGE_MCP_SERVER_URL`` (e.g. ``http://localhost:8765``).
* ``stdio`` — spawn a subprocess MCP server. Requires
  ``METAFORGE_MCP_SERVER_CMD`` (a shell-quoted command, e.g.
  ``python -m metaforge.mcp --transport stdio``).

Every external mode degrades gracefully: if the server can't be
reached, the factory logs a warning and returns the supplied
fallback bridge instead of crashing the gateway. Force a hard fail
with ``METAFORGE_REQUIRE_MCP=true`` for production deploys.
"""

from __future__ import annotations

import os
import shlex
from typing import Any

import structlog

from mcp_core.client import McpClient
from mcp_core.schemas import ToolManifest
from mcp_core.transports import HttpTransport, StdioTransport
from skill_registry.mcp_bridge import InMemoryMcpBridge, McpBridge
from skill_registry.mcp_client_bridge import McpClientBridge

logger = structlog.get_logger(__name__)


_DEFAULT_ADAPTER_ID = "metaforge"
_DEFAULT_READY_SIGNAL = "metaforge-mcp ready"


async def create_mcp_bridge(
    *,
    fallback: McpBridge | None = None,
    require: bool | None = None,
) -> McpBridge:
    """Build the gateway's ``McpBridge`` per env-driven config.

    Parameters
    ----------
    fallback
        Bridge to return when the configured external mode can't be
        reached. Defaults to a fresh ``InMemoryMcpBridge`` so callers
        without a ToolRegistry still get a usable object.
    require
        Override for ``METAFORGE_REQUIRE_MCP``. ``True`` raises on any
        connection failure; ``False`` falls back. Default reads the
        env var (defaults to fall-back).
    """
    mode = (os.environ.get("METAFORGE_MCP_BRIDGE") or "registry").lower()
    if require is None:
        require = os.environ.get("METAFORGE_REQUIRE_MCP", "").lower() == "true"
    fb = fallback or InMemoryMcpBridge()

    if mode == "registry" or mode == "in_memory":
        # Caller (gateway) already owns the registry-backed bridge;
        # nothing for us to do but echo the fallback.
        return fb

    # MET-338: optional client-side API key. ``METAFORGE_MCP_CLIENT_KEY``
    # is sent on every outbound request (HTTP) or propagated to the
    # spawned subprocess (stdio). Falls through to ``METAFORGE_MCP_API_KEY``
    # for symmetric local dev use.
    api_key = (
        os.environ.get("METAFORGE_MCP_CLIENT_KEY")
        or os.environ.get("METAFORGE_MCP_API_KEY")
        or None
    )

    if mode == "http":
        url = os.environ.get("METAFORGE_MCP_SERVER_URL")
        if not url:
            return _fallback_or_raise(
                "METAFORGE_MCP_BRIDGE=http set but METAFORGE_MCP_SERVER_URL is empty",
                fb,
                require,
            )
        transport = HttpTransport(url, api_key=api_key)
        return await _connect_and_wrap("http", url, transport, fb, require)

    if mode == "stdio":
        cmd = os.environ.get("METAFORGE_MCP_SERVER_CMD")
        if not cmd:
            return _fallback_or_raise(
                "METAFORGE_MCP_BRIDGE=stdio set but METAFORGE_MCP_SERVER_CMD is empty",
                fb,
                require,
            )
        ready = os.environ.get("METAFORGE_MCP_READY_SIGNAL", _DEFAULT_READY_SIGNAL)
        transport = StdioTransport(
            command=shlex.split(cmd),
            ready_signal=ready,
            api_key=api_key,
        )
        return await _connect_and_wrap("stdio", cmd, transport, fb, require)

    return _fallback_or_raise(
        f"Unknown METAFORGE_MCP_BRIDGE value: {mode!r}",
        fb,
        require,
    )


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------


async def _connect_and_wrap(
    mode: str,
    target: str,
    transport: Any,
    fallback: McpBridge,
    require: bool,
) -> McpBridge:
    client = McpClient()
    try:
        await client.connect(_DEFAULT_ADAPTER_ID, transport)
        await _discover_tools(client, transport)
    except Exception as exc:  # noqa: BLE001 — we explicitly want to fall back
        logger.warning(
            "mcp_bridge_connect_failed",
            mode=mode,
            target=target,
            error=str(exc),
        )
        try:
            await transport.disconnect()
        except Exception:  # noqa: BLE001 — cleanup, best effort
            pass
        return _fallback_or_raise(
            f"MCP bridge ({mode}) failed to connect to {target}: {exc}",
            fallback,
            require,
            cause=exc,
        )

    bridge = McpClientBridge(client)
    logger.info(
        "mcp_bridge_connected",
        mode=mode,
        target=target,
        tool_count=len(client._manifests),  # noqa: SLF001 — internal but stable
    )
    return bridge


async def _discover_tools(client: McpClient, transport: Any) -> None:
    """Run ``tool/list`` so the client populates its manifest cache.

    The server's response feeds ``client._adapter_for_tool`` so future
    ``call_tool`` invocations route correctly.
    """
    import json

    request = json.dumps({"jsonrpc": "2.0", "id": "discover", "method": "tool/list", "params": {}})
    raw = await transport.send(request)
    payload = json.loads(raw)
    if "error" in payload:
        raise RuntimeError(f"tool/list returned an error: {payload['error']}")
    tools = payload.get("result", {}).get("tools", [])
    for tool_data in tools:
        client.register_manifest(
            ToolManifest(
                tool_id=tool_data["tool_id"],
                adapter_id=tool_data.get("adapter_id", _DEFAULT_ADAPTER_ID),
                name=tool_data["name"],
                description=tool_data.get("description", ""),
                capability=tool_data.get("capability", ""),
                input_schema=tool_data.get("input_schema", {}),
                output_schema=tool_data.get("output_schema", {}),
                phase=tool_data.get("phase", 1),
            )
        )


def _fallback_or_raise(
    message: str,
    fallback: McpBridge,
    require: bool,
    cause: Exception | None = None,
) -> McpBridge:
    if require:
        if cause is not None:
            raise RuntimeError(message) from cause
        raise RuntimeError(message)
    logger.warning("mcp_bridge_fallback", reason=message)
    return fallback
