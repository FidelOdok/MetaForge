"""UAT-C2-L1 — Standalone MCP server + bridge (MET-337, MET-306).

Acceptance bullets validated:

* MET-337: ``python -m metaforge.mcp --transport stdio`` boots, emits
  ``metaforge-mcp ready`` on stderr, exposes ≥7 tools.
* MET-306: ``StdioTransport`` connects to a subprocess MCP server;
  ``McpClientBridge`` enforces ``asyncio.wait_for`` timeout; the bridge
  factory falls back gracefully on connection failure.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

import pytest

from skill_registry.bridge_factory import create_mcp_bridge
from skill_registry.mcp_bridge import InMemoryMcpBridge, McpToolError
from skill_registry.mcp_client_bridge import McpClientBridge
from tests.uat.conftest import assert_validates, spawn_metaforge_mcp

pytestmark = [pytest.mark.uat, pytest.mark.integration]


# ---------------------------------------------------------------------------
# MET-337 — Subprocess boot + tool/list
# ---------------------------------------------------------------------------


async def test_met337_subprocess_boots_and_lists_seven_tools() -> None:
    import warnings

    warnings.filterwarnings(
        "ignore",
        message=".*Event loop is closed.*",
        category=pytest.PytestUnraisableExceptionWarning,
    )
    client, _ = await spawn_metaforge_mcp(adapters="cadquery,calculix")
    try:
        tools = await client.list_tools()  # type: ignore[attr-defined]
        count = len(tools)
    finally:
        await client.disconnect("metaforge")  # type: ignore[attr-defined]

    assert_validates(
        "MET-337",
        "tool/list returns ≥7 tools through the spawned subprocess",
        count >= 7,
        f"got {count} tools",
    )


# ---------------------------------------------------------------------------
# MET-306 — Bridge timeout enforcement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_met306_bridge_enforces_timeout() -> None:
    """``McpClientBridge.invoke(timeout=…)`` raises ``McpToolError`` when
    the underlying call exceeds the deadline."""
    from mcp_core.schemas import ToolCallRequest, ToolCallResult

    class _Slow:
        async def call_tool(self, req: ToolCallRequest) -> ToolCallResult:
            await asyncio.sleep(2.0)
            return ToolCallResult(tool_id=req.tool_id, status="success", data={}, duration_ms=0.0)

        async def list_tools(self, *a: Any, **k: Any) -> list[Any]:
            return []

    bridge = McpClientBridge(_Slow())  # type: ignore[arg-type]
    with pytest.raises(McpToolError, match="timed out"):
        await bridge.invoke("any.tool", {}, timeout=1)


# ---------------------------------------------------------------------------
# MET-306 — Factory falls back gracefully
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_met306_factory_falls_back_when_command_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unreachable stdio server falls back to the supplied bridge."""
    monkeypatch.setenv("METAFORGE_MCP_BRIDGE", "stdio")
    monkeypatch.setenv(
        "METAFORGE_MCP_SERVER_CMD",
        f"{sys.executable} -c 'import sys; sys.exit(1)'",
    )
    fallback = InMemoryMcpBridge()
    bridge = await create_mcp_bridge(fallback=fallback)
    assert_validates(
        "MET-306",
        "factory returned the fallback bridge on subprocess failure",
        bridge is fallback,
        f"got bridge type: {type(bridge).__name__}",
    )


@pytest.mark.asyncio
async def test_met306_factory_require_flag_raises() -> None:
    """``METAFORGE_REQUIRE_MCP=true`` flips fallback into a hard fail."""
    import os

    save = {k: os.environ.get(k) for k in ("METAFORGE_MCP_BRIDGE", "METAFORGE_REQUIRE_MCP")}
    os.environ["METAFORGE_MCP_BRIDGE"] = "http"
    os.environ["METAFORGE_REQUIRE_MCP"] = "true"
    os.environ.pop("METAFORGE_MCP_SERVER_URL", None)
    try:
        with pytest.raises(RuntimeError, match="METAFORGE_MCP_SERVER_URL"):
            await create_mcp_bridge(fallback=InMemoryMcpBridge())
    finally:
        for k, v in save.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
