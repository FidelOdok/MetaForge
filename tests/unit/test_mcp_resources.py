"""Resources are reachable, and a short list is never silent (FORGE-355).

Adapters have supported `resources/list` and `resources/read` since MET-384,
and the knowledge adapter registers several. None of them were reachable: the
unified server dispatched neither method and advertised no capability, so a
spec-compliant client got "Unknown method" and had no way to learn they
existed.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from metaforge.mcp.server import UnifiedMcpServer
from tool_registry.mcp_server.handlers import ResourceManifestEntry
from tool_registry.mcp_server.server import McpToolServer


class _Twin(McpToolServer):
    def __init__(self) -> None:
        super().__init__(adapter_id="twin", version="0.1.0")
        self.register_resource(
            ResourceManifestEntry(
                uri_template="metaforge://twin/brief/{project_id}",
                adapter_id="twin",
                name="Project brief",
                description="Newest work first",
                mime_type="text/markdown",
            ),
            self._read,
            lambda uri: uri.startswith("metaforge://twin/brief/"),
        )

    async def _read(self, uri: str) -> list[dict[str, Any]]:
        return [{"uri": uri, "mimeType": "text/markdown", "text": "# Brief"}]


class _Knowledge(McpToolServer):
    def __init__(self) -> None:
        super().__init__(adapter_id="knowledge", version="0.1.0")
        self.register_resource(
            ResourceManifestEntry(
                uri_template="metaforge://knowledge/doc/{doc_id}",
                adapter_id="knowledge",
                name="Ingested document",
                description="A source document",
                mime_type="text/plain",
            ),
            self._read,
            lambda uri: uri.startswith("metaforge://knowledge/doc/"),
        )

    async def _read(self, uri: str) -> list[dict[str, Any]]:
        return [{"uri": uri, "mimeType": "text/plain", "text": "doc"}]


class _Down(McpToolServer):
    """An adapter whose container is not running."""

    def __init__(self) -> None:
        super().__init__(adapter_id="calculix", version="0.1.0")

    async def handle_request(self, raw: str) -> str:
        raise RuntimeError("adapter container is down (-32001)")


async def _rpc(server: UnifiedMcpServer, method: str, params: dict | None = None) -> dict:
    raw = await server.handle_request(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})
    )
    return json.loads(raw)


@pytest.mark.asyncio
class TestReachable:
    async def test_the_capability_is_advertised(self) -> None:
        # Without this a client never asks, so the resources may as well not
        # exist however well they are registered.
        result = (await _rpc(UnifiedMcpServer(adapters=[_Twin()]), "initialize"))["result"]
        assert "resources" in result["capabilities"]

    async def test_resources_from_every_adapter_are_listed(self) -> None:
        # FORGE-337: everything MetaForge publishes is parameterised by
        # project, so it is all templates and belongs in
        # resources/templates/list under the spec's own field names. It was
        # coming back from resources/list keyed `uri_template`, which is
        # neither the method nor the field a compliant client reads.
        server = UnifiedMcpServer(adapters=[_Twin(), _Knowledge()])
        result = (await _rpc(server, "resources/templates/list"))["result"]
        names = {r["name"] for r in result["resourceTemplates"]}
        assert names == {"Project brief", "Ingested document"}
        assert all("uriTemplate" in r for r in result["resourceTemplates"])
        assert all("uri_template" not in r for r in result["resourceTemplates"])

    async def test_resources_list_holds_only_concrete_resources(self) -> None:
        """A template has no `uri`, so returning one here gives a compliant
        client an entry it cannot address. Empty is the honest answer."""
        server = UnifiedMcpServer(adapters=[_Twin(), _Knowledge()])
        assert (await _rpc(server, "resources/list"))["result"]["resources"] == []

    async def test_a_resource_can_be_read(self) -> None:
        server = UnifiedMcpServer(adapters=[_Twin()])
        result = (await _rpc(server, "resources/read", {"uri": "metaforge://twin/brief/p-1"}))[
            "result"
        ]
        assert result["contents"][0]["text"] == "# Brief"


@pytest.mark.asyncio
class TestNothingVanishes:
    async def test_a_down_adapter_is_reported_not_dropped(self) -> None:
        # Same rule as tools/list (FORGE-339). A short resource list reads to
        # a model as "that context does not exist".
        server = UnifiedMcpServer(adapters=[_Twin(), _Down()])
        result = (await _rpc(server, "resources/templates/list"))["result"]
        assert len(result["resourceTemplates"]) == 1
        reported = {a["adapter_id"] for a in result["_meta"]["unavailableAdapters"]}
        assert "calculix" in reported

    async def test_a_healthy_server_adds_no_meta(self) -> None:
        server = UnifiedMcpServer(adapters=[_Twin()])
        result = (await _rpc(server, "resources/templates/list"))["result"]
        assert "_meta" not in result


@pytest.mark.asyncio
class TestRoutingAndErrors:
    async def test_reads_route_on_the_uri_not_on_registration_order(self) -> None:
        # Asking every adapter and taking the first match would make the
        # answer depend on the order adapters happen to be constructed in --
        # something no caller can see or control.
        forward = UnifiedMcpServer(adapters=[_Twin(), _Knowledge()])
        backward = UnifiedMcpServer(adapters=[_Knowledge(), _Twin()])
        uri = "metaforge://knowledge/doc/d-1"
        a = (await _rpc(forward, "resources/read", {"uri": uri}))["result"]
        b = (await _rpc(backward, "resources/read", {"uri": uri}))["result"]
        assert a == b

    async def test_an_unknown_adapter_names_the_loaded_ones(self) -> None:
        server = UnifiedMcpServer(adapters=[_Twin()])
        response = await _rpc(server, "resources/read", {"uri": "metaforge://nosuch/x/1"})
        assert "error" in response
        assert "twin" in response["error"]["message"]

    async def test_errors_come_back_as_json_rpc_not_as_a_dropped_connection(self) -> None:
        # The same mistake was made on the approval path: an exception
        # escaping handle_request reaches a client as a closed socket, which
        # tells it nothing about what went wrong.
        server = UnifiedMcpServer(adapters=[_Twin()])
        for params in ({}, {"uri": "http://elsewhere/x"}, {"uri": "metaforge://nosuch/x"}):
            response = await _rpc(server, "resources/read", params)
            assert response["jsonrpc"] == "2.0"
            assert "error" in response, params
