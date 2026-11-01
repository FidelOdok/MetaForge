"""Unknown-tool errors name tools that exist, and near-miss ids resolve (FORGE-343).

MetaForge tool ids are dotted (``twin.get_node``) but models routinely send
``twin_get_node`` or ``twin/get_node``. FORGE-236 fixed this on the harness
side after a bare "not found" was observed making a model conclude a healthy
backend was down — it cannot tell "you typed the name wrong" from "the server
is broken" unless the error says which. The MCP path, which is the one every
external harness uses, never got the same treatment.
"""

from __future__ import annotations

from typing import Any

import pytest

from metaforge.mcp.server import UnifiedMcpServer
from tool_registry.mcp_server.handlers import ToolManifest, ToolNotFoundError
from tool_registry.mcp_server.server import McpToolServer


def _manifest(tool_id: str, adapter_id: str) -> ToolManifest:
    return ToolManifest(
        tool_id=tool_id,
        adapter_id=adapter_id,
        name=tool_id,
        description=f"stub {tool_id}",
        capability="test",
    )


class _TwinStub(McpToolServer):
    def __init__(self) -> None:
        super().__init__(adapter_id="twin", version="0.1.0")
        for tid in ("twin.get_node", "twin.record_decision", "twin.query_cypher"):
            self.register_tool(_manifest(tid, "twin"), self._ok)

    async def _ok(self, args: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True}


class _UsdStub(McpToolServer):
    """Adapter id with an underscore in it — the reason resolution cannot
    just split on the first separator."""

    def __init__(self) -> None:
        super().__init__(adapter_id="omniverse_usd", version="0.1.0")
        self.register_tool(_manifest("omniverse_usd.describe_stage", "omniverse_usd"), self._ok)

    async def _ok(self, args: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True}


@pytest.fixture
def server() -> UnifiedMcpServer:
    return UnifiedMcpServer(adapters=[_TwinStub(), _UsdStub()])


class TestAliasResolution:
    @pytest.mark.parametrize(
        "requested",
        ["twin.get_node", "twin_get_node", "twin/get_node", "TWIN.GET_NODE", "twin-get-node"],
    )
    def test_every_spelling_reaches_the_same_tool(
        self, server: UnifiedMcpServer, requested: str
    ) -> None:
        assert server._resolve_tool_id(requested) == "twin.get_node"

    def test_an_underscored_adapter_id_still_resolves(self, server: UnifiedMcpServer) -> None:
        # omniverse_usd.describe_stage has separators on both sides of the
        # dot; splitting on the first one would look for adapter "omniverse".
        assert server._resolve_tool_id("omniverse_usd_describe_stage") == (
            "omniverse_usd.describe_stage"
        )

    def test_an_ambiguous_alias_is_not_guessed(self) -> None:
        # Two tools whose slugs collide. Guessing which one to run is how a
        # read turns into a write, so neither is chosen.
        class _Collide(McpToolServer):
            def __init__(self) -> None:
                super().__init__(adapter_id="a", version="0.1.0")
                self.register_tool(_manifest("a.b_c", "a"), self._ok)
                self.register_tool(_manifest("a_b.c", "a"), self._ok)

            async def _ok(self, args: dict[str, Any]) -> dict[str, Any]:
                return {}

        srv = UnifiedMcpServer(adapters=[_Collide()])
        with pytest.raises(ToolNotFoundError):
            srv._resolve_tool_id("abc")


class TestSuggestions:
    def test_a_typo_names_the_tool_it_is_near(self, server: UnifiedMcpServer) -> None:
        with pytest.raises(ToolNotFoundError) as exc:
            server._resolve_tool_id("twin.get_nodes")
        assert "twin.get_node" in exc.value.did_you_mean

    def test_the_message_itself_carries_them(self, server: UnifiedMcpServer) -> None:
        # A client that renders only the message still shows the suggestion.
        with pytest.raises(ToolNotFoundError) as exc:
            server._resolve_tool_id("twin.get_nodes")
        assert "twin.get_node" in str(exc.value)

    def test_an_unrecognisable_name_still_names_the_adapter_family(
        self, server: UnifiedMcpServer
    ) -> None:
        with pytest.raises(ToolNotFoundError) as exc:
            server._resolve_tool_id("twin.absolutely_nothing_like_this_at_all")
        assert exc.value.did_you_mean, "should fall back to the adapter's own tools"
        assert all(t.startswith("twin") for t in exc.value.did_you_mean)

    def test_an_empty_tool_id_lists_what_is_available(self, server: UnifiedMcpServer) -> None:
        with pytest.raises(ToolNotFoundError) as exc:
            server._resolve_tool_id("")
        assert exc.value.did_you_mean


@pytest.mark.asyncio
class TestOverTheWire:
    async def test_the_error_envelope_carries_the_suggestions(
        self, server: UnifiedMcpServer
    ) -> None:
        import json

        raw = await server.handle_request(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "twin.get_nodes", "arguments": {}},
                }
            )
        )
        data = json.loads(raw)["error"]["data"]
        assert data["tool_id"] == "twin.get_nodes"
        assert "twin.get_node" in data["did_you_mean"]
        # 4 adapter tools + the server's own health.check (FORGE-409).
        assert data["tool_count"] == 5

    async def test_an_aliased_call_actually_runs(self, server: UnifiedMcpServer) -> None:
        import json

        raw = await server.handle_request(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "twin_get_node", "arguments": {}},
                }
            )
        )
        assert "error" not in json.loads(raw), json.loads(raw)
