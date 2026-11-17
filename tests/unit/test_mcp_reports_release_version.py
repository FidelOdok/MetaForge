"""The MCP server reports the release it runs, like the gateway does.

Staging's gateway said ``0.3.797`` while the MCP server on the same image
said ``0.1.0`` in ``initialize``, ``/health`` and ``health/check``: the
gateway read the installed distribution (which the release workflow stamps),
and the MCP server carried a literal. Both now read ``metaforge.version``.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

import metaforge.version as version_module
from api_gateway.version import gateway_version
from metaforge.mcp.server import UnifiedMcpServer
from metaforge.version import UNKNOWN, package_version
from tool_registry.mcp_server.server import McpToolServer


class _Adapter(McpToolServer):
    def __init__(self) -> None:
        super().__init__(adapter_id="twin", version="0.1.0")


def _call(server: UnifiedMcpServer, method: str, params: dict[str, Any]) -> dict[str, Any]:
    raw = asyncio.run(
        server.handle_request(
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        )
    )
    return dict(json.loads(raw)["result"])


def _initialize(server: UnifiedMcpServer) -> dict[str, Any]:
    return _call(
        server,
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "0"},
        },
    )


def test_the_gateway_and_the_mcp_server_report_one_version() -> None:
    assert gateway_version() == package_version()
    server = UnifiedMcpServer([_Adapter()])
    assert _initialize(server)["serverInfo"]["version"] == package_version()
    assert _call(server, "health/check", {})["version"] == package_version()


def test_the_stamped_release_is_what_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(version_module, "_distribution_version", lambda _name: "0.3.797")
    server = UnifiedMcpServer([_Adapter()])
    assert _initialize(server)["serverInfo"] == {"name": "metaforge-mcp", "version": "0.3.797"}
    assert _call(server, "health/check", {})["version"] == "0.3.797"


def test_an_uninstalled_checkout_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    def _missing(_name: str) -> str:
        raise version_module.PackageNotFoundError(_name)

    monkeypatch.setattr(version_module, "_distribution_version", _missing)
    assert package_version() == UNKNOWN
    assert _initialize(UnifiedMcpServer([_Adapter()]))["serverInfo"]["version"] == UNKNOWN


def test_a_caller_can_still_name_the_version() -> None:
    server = UnifiedMcpServer([_Adapter()], version="9.9.9")
    assert _initialize(server)["serverInfo"]["version"] == "9.9.9"
