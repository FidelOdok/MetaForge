"""health/check has to be able to say unhealthy (FORGE-332).

It used to return ``status: "healthy"`` unconditionally, and list each
adapter's tool count from *registration* rather than from asking it anything.
A gateway with every CAD container down answered "healthy" with a full
adapter list.

That is worse than the tools/list problem it resembles, where the tool count
at least shrank: a health report that cannot say unhealthy is the one thing a
doctor reads, and `/metaforge:doctor` is built on this.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from metaforge.mcp.server import UnifiedMcpServer
from tool_registry.mcp_server.handlers import ToolManifest
from tool_registry.mcp_server.server import McpToolServer


def _manifest(tool_id: str, adapter_id: str) -> ToolManifest:
    return ToolManifest(
        tool_id=tool_id,
        adapter_id=adapter_id,
        name=tool_id,
        description="stub",
        capability="test",
    )


class _Healthy(McpToolServer):
    def __init__(self) -> None:
        super().__init__(adapter_id="twin", version="0.1.0")
        self.register_tool(_manifest("twin.get_node", "twin"), self._ok)

    async def _ok(self, args: dict[str, Any]) -> dict[str, Any]:
        return {}


class _Down(McpToolServer):
    """Its container is not running — the normal dev condition."""

    def __init__(self) -> None:
        super().__init__(adapter_id="calculix", version="0.1.0")
        self.register_tool(_manifest("calculix.run_fea", "calculix"), self._ok)

    async def _ok(self, args: dict[str, Any]) -> dict[str, Any]:
        return {}

    async def handle_request(self, raw: str) -> str:
        raise RuntimeError("adapter container is down (-32001)")


class _Hangs(McpToolServer):
    """Answers eventually. Readiness cannot wait that long."""

    def __init__(self) -> None:
        super().__init__(adapter_id="freecad", version="0.1.0")

    async def handle_request(self, raw: str) -> str:
        await asyncio.sleep(30)
        return "{}"


async def _health(server: UnifiedMcpServer) -> dict[str, Any]:
    raw = await server.handle_request(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "health/check", "params": {}})
    )
    return json.loads(raw)["result"]


@pytest.mark.asyncio
class TestItCanSayUnhealthy:
    async def test_a_down_adapter_makes_the_service_degraded(self) -> None:
        report = await _health(UnifiedMcpServer(adapters=[_Healthy(), _Down()]))
        assert report["status"] == "degraded"
        assert report["unreachable_adapters"] == ["calculix"]

    async def test_the_reason_survives_to_the_report(self) -> None:
        # "container is down" and "no answer in time" call for different
        # actions, so they must not collapse into one word.
        report = await _health(UnifiedMcpServer(adapters=[_Down()]))
        entry = next(a for a in report["adapters"] if a["adapter_id"] == "calculix")
        assert entry["reachable"] is False
        assert "-32001" in entry["error"]

    async def test_a_hang_is_bounded_and_reported(self) -> None:
        server = UnifiedMcpServer(adapters=[_Healthy(), _Hangs()])
        server._PROBE_TIMEOUT_SECONDS = 0.2
        report = await asyncio.wait_for(_health(server), timeout=5.0)
        entry = next(a for a in report["adapters"] if a["adapter_id"] == "freecad")
        assert entry["reachable"] is False
        assert "no answer" in entry["error"]

    async def test_everything_up_is_still_healthy(self) -> None:
        # The other half: this must not become a check that always complains,
        # or it gets ignored exactly like one that never does.
        report = await _health(UnifiedMcpServer(adapters=[_Healthy()]))
        assert report["status"] == "healthy"
        assert "unreachable_adapters" not in report
        assert "detail" not in report


@pytest.mark.asyncio
class TestItStaysUsableAsAReadinessProbe:
    async def test_the_report_still_answers_when_every_adapter_is_down(self) -> None:
        # The MCP server is up even when its adapters are not. Failing the
        # whole endpoint would have an orchestrator restart the gateway to
        # cure a sick CAD container.
        report = await _health(UnifiedMcpServer(adapters=[_Down()]))
        assert report["service"] == "metaforge-mcp"
        assert report["status"] == "degraded"
        # +1 for the server's own `health.check` (FORGE-409), which is the
        # tool that makes this report reachable without a shell -- so it is
        # present precisely when every adapter is down.
        assert report["tool_count"] == 2

    async def test_registered_tools_are_still_counted(self) -> None:
        # Reported as `tools_registered`, not `tools_available` -- they are
        # registered, and unreachable, and saying so is the point.
        report = await _health(UnifiedMcpServer(adapters=[_Down()]))
        entry = report["adapters"][0]
        assert entry["tools_registered"] == 1
        assert entry["reachable"] is False
