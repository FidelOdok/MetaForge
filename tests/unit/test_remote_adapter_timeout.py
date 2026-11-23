"""A remote adapter's calls may run as long as its slowest declared tool.

The MCP server reached each adapter container over an HTTP transport with a
flat 120 s timeout. A 4-layer board's autoroute (declared 900 s) ran past it:
the server answered with nothing while Freerouting kept routing in the
adapter. The adapter's tool listing already carries each tool's
``resource_limits.max_cpu_seconds``; the connection now takes the longest one
plus a margin.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from mcp_core.transports import HttpTransport
from tool_registry import bootstrap


def test_the_timeout_only_grows() -> None:
    transport = HttpTransport("http://adapter:8103")
    assert transport.timeout == 120.0
    transport.extend_timeout(60)
    assert transport.timeout == 120.0
    transport.extend_timeout(960)
    assert transport.timeout == 960.0


class _FakeTransport(HttpTransport):
    listing: dict[str, Any] = {}

    async def connect(self) -> None:
        self._connected = True

    def is_connected(self) -> bool:
        return True

    async def send(self, message: str) -> str:
        return json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"tools": self.listing["tools"]}})


def _tool(tool_id: str, cpu: float | None) -> dict[str, Any]:
    tool: dict[str, Any] = {"tool_id": tool_id, "adapter_id": "kicad", "name": tool_id}
    if cpu is not None:
        tool["resource_limits"] = {"max_cpu_seconds": cpu}
    return tool


@pytest.mark.parametrize(
    ("tools", "expected"),
    [
        ([_tool("kicad.run_erc", 120), _tool("kicad.autoroute_pcb", 900)], 960.0),
        ([_tool("kicad.run_erc", 30)], 120.0),
        ([_tool("kicad.run_erc", None)], 120.0),
    ],
)
async def test_the_connection_takes_the_slowest_declared_tool(
    monkeypatch: pytest.MonkeyPatch, tools: list[dict[str, Any]], expected: float
) -> None:
    _FakeTransport.listing = {"tools": tools}
    made: list[_FakeTransport] = []

    def factory(url: str) -> _FakeTransport:
        t = _FakeTransport(url)
        made.append(t)
        return t

    monkeypatch.setattr(bootstrap, "HttpTransport", factory)
    await bootstrap._create_remote_adapter("kicad", "http://kicad-adapter:8103")
    assert made[0].timeout == expected
