"""Unit tests for ``metaforge.mcp.server.UnifiedMcpServer`` (MET-337).

Uses lightweight in-process ``McpToolServer`` subclasses as stubs so
the routing / aggregation / dispatch logic can be exercised without
booting the real tool_registry adapters (which pull in CalculiX,
LightRAG, etc.).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from metaforge.mcp.server import UnifiedMcpServer
from tool_registry.mcp_server.handlers import ToolManifest
from tool_registry.mcp_server.server import McpToolServer


def _manifest(tool_id: str, adapter_id: str, capability: str = "test") -> ToolManifest:
    return ToolManifest(
        tool_id=tool_id,
        adapter_id=adapter_id,
        name=tool_id,
        description=f"stub for {tool_id}",
        capability=capability,
    )


class _AlphaServer(McpToolServer):
    """Two tools that just echo their args."""

    def __init__(self) -> None:
        super().__init__(adapter_id="alpha", version="0.1.0")
        self.register_tool(_manifest("alpha.add", "alpha", "math"), self._add)
        self.register_tool(_manifest("alpha.mul", "alpha", "math"), self._mul)

    async def _add(self, args: dict[str, Any]) -> dict[str, Any]:
        return {"sum": args["a"] + args["b"]}

    async def _mul(self, args: dict[str, Any]) -> dict[str, Any]:
        return {"product": args["a"] * args["b"]}


class _BetaServer(McpToolServer):
    """One healthy tool, one that always raises."""

    def __init__(self) -> None:
        super().__init__(adapter_id="beta", version="0.1.0")
        self.register_tool(_manifest("beta.ping", "beta", "diag"), self._ping)
        self.register_tool(_manifest("beta.boom", "beta", "diag"), self._boom)

    async def _ping(self, args: dict[str, Any]) -> dict[str, Any]:
        return {"pong": True}

    async def _boom(self, args: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("intentional failure")


@pytest.fixture
def server() -> UnifiedMcpServer:
    return UnifiedMcpServer(adapters=[_AlphaServer(), _BetaServer()])


def _request(method: str, params: dict[str, Any] | None = None, id_: str = "1") -> str:
    return json.dumps({"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}})


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


class TestToolList:
    @pytest.mark.asyncio
    async def test_aggregates_across_adapters(self, server: UnifiedMcpServer) -> None:
        raw = await server.handle_request(_request("tool/list"))
        body = json.loads(raw)
        ids = sorted(t["tool_id"] for t in body["result"]["tools"])
        assert ids == ["alpha.add", "alpha.mul", "beta.boom", "beta.ping"]

    @pytest.mark.asyncio
    async def test_capability_filter_passthrough(self, server: UnifiedMcpServer) -> None:
        raw = await server.handle_request(_request("tool/list", {"capability": "math"}))
        body = json.loads(raw)
        ids = sorted(t["tool_id"] for t in body["result"]["tools"])
        assert ids == ["alpha.add", "alpha.mul"]


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


class TestToolCall:
    @pytest.mark.asyncio
    async def test_routes_to_owning_adapter(self, server: UnifiedMcpServer) -> None:
        raw = await server.handle_request(
            _request("tool/call", {"tool_id": "alpha.add", "arguments": {"a": 2, "b": 5}})
        )
        body = json.loads(raw)
        assert body["result"]["data"] == {"sum": 7}
        assert body["result"]["status"] == "success"

    @pytest.mark.asyncio
    async def test_routes_other_adapter(self, server: UnifiedMcpServer) -> None:
        raw = await server.handle_request(
            _request("tool/call", {"tool_id": "beta.ping", "arguments": {}})
        )
        body = json.loads(raw)
        assert body["result"]["data"] == {"pong": True}

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_method_not_found(self, server: UnifiedMcpServer) -> None:
        raw = await server.handle_request(
            _request("tool/call", {"tool_id": "gamma.missing", "arguments": {}})
        )
        body = json.loads(raw)
        assert "error" in body
        assert body["error"]["code"] == -32601
        assert body["error"]["data"]["tool_id"] == "gamma.missing"

    @pytest.mark.asyncio
    async def test_handler_failure_propagates_as_execution_error(
        self, server: UnifiedMcpServer
    ) -> None:
        raw = await server.handle_request(
            _request("tool/call", {"tool_id": "beta.boom", "arguments": {}})
        )
        body = json.loads(raw)
        assert "error" in body
        assert body["error"]["code"] == -32001
        assert body["error"]["data"]["tool_id"] == "beta.boom"


# ---------------------------------------------------------------------------
# Health + protocol hygiene
# ---------------------------------------------------------------------------


class TestHealth:
    @pytest.mark.asyncio
    async def test_health_aggregates_adapters(self, server: UnifiedMcpServer) -> None:
        raw = await server.handle_request(_request("health/check"))
        body = json.loads(raw)
        result = body["result"]
        assert result["service"] == "metaforge-mcp"
        assert result["adapter_count"] == 2
        assert result["tool_count"] == 4
        assert sorted(a["adapter_id"] for a in result["adapters"]) == ["alpha", "beta"]


class TestProtocolHygiene:
    @pytest.mark.asyncio
    async def test_invalid_json_returns_invalid_request(self, server: UnifiedMcpServer) -> None:
        raw = await server.handle_request("not json at all")
        body = json.loads(raw)
        assert body["error"]["code"] == -32600

    @pytest.mark.asyncio
    async def test_missing_jsonrpc_field_rejected(self, server: UnifiedMcpServer) -> None:
        raw = await server.handle_request(json.dumps({"id": "1", "method": "tool/list"}))
        body = json.loads(raw)
        assert body["error"]["code"] == -32600

    @pytest.mark.asyncio
    async def test_unknown_method_returns_method_not_found(self, server: UnifiedMcpServer) -> None:
        raw = await server.handle_request(_request("tool/run"))
        body = json.loads(raw)
        assert body["error"]["code"] == -32601


# ---------------------------------------------------------------------------
# Construction guards
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_tool_id_collision_rejected(self) -> None:
        a = _AlphaServer()
        # Build a second alpha-named server that re-registers alpha.add
        clone = _AlphaServer()
        with pytest.raises(ValueError, match="collision"):
            UnifiedMcpServer(adapters=[a, clone])

    def test_empty_adapter_list_is_legal(self) -> None:
        server = UnifiedMcpServer(adapters=[])
        assert server.tool_ids == []
        assert server.adapters == []
