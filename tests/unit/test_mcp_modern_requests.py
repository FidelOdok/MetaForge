"""Handshake-free (2026-07-28) requests: discover, resultType, headers (FORGE-600).

Served by default since FORGE-592; ``METAFORGE_MCP_MODERN=off`` turns it off.
"""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Any

import pytest

from mcp_core.elicitation import ElicitAction, ElicitResult
from mcp_core.guardrails import Caller
from mcp_core.protocol_versions import (
    CACHE_HINTS,
    HEADER_MISMATCH,
    INVALID_PARAMS,
    META_CLIENT_CAPABILITIES,
    META_PROTOCOL_VERSION,
    META_SERVER_INFO,
    MODERN_PREVIEW_ENV,
    current_modern_request,
    is_modern,
    modern_header_problem,
    modern_status,
    supported_versions,
)
from metaforge.mcp.server import UnifiedMcpServer

MODERN = "2026-07-28"


@pytest.fixture(autouse=True)
def _preview(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MODERN_PREVIEW_ENV, "on")


def _meta(caps: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        META_PROTOCOL_VERSION: MODERN,
        "io.modelcontextprotocol/clientInfo": {"name": "modern-client", "version": "1"},
        META_CLIENT_CAPABILITIES: caps if caps is not None else {},
    }


def _call(server: UnifiedMcpServer, method: str, params: dict[str, Any]) -> dict[str, Any]:
    raw = asyncio.run(
        server.handle_request(
            json.dumps({"jsonrpc": "2.0", "id": 9, "method": method, "params": params})
        )
    )
    return dict(json.loads(raw))


class TestTheSwitch:
    def test_the_preview_serves_2026_07_28(self) -> None:
        assert is_modern(MODERN)
        assert MODERN in supported_versions()

    def test_the_switch_turns_it_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("METAFORGE_MCP_MODERN", "off")
        response = _call(UnifiedMcpServer(adapters=[]), "tools/list", {"_meta": _meta()})
        assert response["error"]["code"] == -32022

    def test_the_old_preview_switch_changes_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(MODERN_PREVIEW_ENV, raising=False)
        assert is_modern(MODERN)


class TestDiscover:
    def test_it_names_versions_capabilities_and_identity(self) -> None:
        result = _call(UnifiedMcpServer(adapters=[]), "server/discover", {"_meta": _meta()})[
            "result"
        ]
        assert result["supportedVersions"][0] == MODERN
        assert "2025-11-25" in result["supportedVersions"]
        assert set(result["capabilities"]) >= {"tools", "resources", "prompts"}
        assert result["_meta"][META_SERVER_INFO]["name"] == "metaforge-mcp"
        assert result["resultType"] == "complete"
        assert (result["ttlMs"], result["cacheScope"]) == CACHE_HINTS["server/discover"]
        assert result["instructions"]

    def test_it_is_not_a_handshake_method(self) -> None:
        # Without a modern version there is no such method.
        response = _call(UnifiedMcpServer(adapters=[]), "server/discover", {})
        assert response["error"]["code"] == -32601


class TestEveryResult:
    def test_a_list_carries_result_type_identity_and_cache_hints(self) -> None:
        result = _call(UnifiedMcpServer(adapters=[]), "tools/list", {"_meta": _meta()})["result"]
        assert result["resultType"] == "complete"
        assert result["_meta"][META_SERVER_INFO]["name"] == "metaforge-mcp"
        assert result["ttlMs"] >= 0 and result["cacheScope"] in {"public", "private"}
        assert "tools" in result

    def test_a_handshake_answer_is_unchanged(self) -> None:
        result = _call(UnifiedMcpServer(adapters=[]), "tools/list", {})["result"]
        assert "resultType" not in result
        assert "ttlMs" not in result

    def test_an_unknown_method_stays_method_not_found(self) -> None:
        assert (
            _call(UnifiedMcpServer(adapters=[]), "ping", {"_meta": _meta()})["error"]["code"]
            == -32601
        )

    def test_a_missing_resource_is_invalid_params_not_32002(self) -> None:
        server = UnifiedMcpServer(adapters=[])
        uri = "metaforge://nothing/here"
        legacy = _call(server, "resources/read", {"uri": uri})
        modern = _call(server, "resources/read", {"uri": uri, "_meta": _meta()})
        assert legacy["error"]["code"] == -32002  # the handshake revisions keep it
        assert modern["error"]["code"] == INVALID_PARAMS


class TestRequiredMeta:
    @pytest.mark.parametrize(
        "meta",
        [
            {META_PROTOCOL_VERSION: MODERN},
            {META_PROTOCOL_VERSION: MODERN, META_CLIENT_CAPABILITIES: "all"},
        ],
    )
    def test_missing_client_capabilities_is_malformed(self, meta: dict[str, Any]) -> None:
        response = _call(UnifiedMcpServer(adapters=[]), "tools/list", {"_meta": meta})
        assert response["error"]["code"] == INVALID_PARAMS
        assert "clientCapabilities" in response["error"]["message"]


class TestNoStateCrossesRequests:
    def test_a_modern_request_never_asks_a_question(self) -> None:
        """2026-07-28 forbids server-initiated requests; MRTR comes in FORGE-601."""

        async def elicitor(_m: str, _s: dict[str, Any]) -> ElicitResult:
            return ElicitResult(ElicitAction.ACCEPT, {})

        server = UnifiedMcpServer(adapters=[], caller=Caller.UNTRUSTED, elicitor=elicitor)
        _call(
            server,
            "initialize",
            {"protocolVersion": "2025-11-25", "capabilities": {"elicitation": {}}},
        )
        assert server.can_elicit is True  # the handshake client can be asked
        seen: list[bool] = []
        original = server._mcp_tools_list

        async def spy(params: dict[str, Any]) -> dict[str, Any]:
            seen.append(server.can_elicit)
            assert current_modern_request() is not None
            return await original(params)

        server._mcp_tools_list = spy  # type: ignore[method-assign]
        _call(server, "tools/list", {"_meta": _meta({"elicitation": {}})})
        assert seen == [False]
        assert server.can_elicit is True  # and the handshake client still can

    def test_it_does_not_overwrite_the_handshake_clients_details(self) -> None:
        server = UnifiedMcpServer(adapters=[])
        _call(server, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})
        _call(server, "tools/list", {"_meta": _meta({"elicitation": {}})})
        assert server._negotiated_protocol == "2025-06-18"
        assert server._client_capabilities == {}


class TestHeaderRules:
    def _message(self, method: str, **params: Any) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}

    def test_complete_headers_pass(self) -> None:
        headers = {"MCP-Protocol-Version": MODERN, "Mcp-Method": "tools/call", "Mcp-Name": "x.y"}
        assert modern_header_problem(headers, self._message("tools/call", name="x.y")) is None

    def test_a_base64_name_is_decoded(self) -> None:
        name = "résumé.tool"
        encoded = "=?base64?" + base64.b64encode(name.encode()).decode() + "?="
        headers = {"MCP-Protocol-Version": MODERN, "Mcp-Method": "tools/call", "Mcp-Name": encoded}
        assert modern_header_problem(headers, self._message("tools/call", name=name)) is None

    @pytest.mark.parametrize(
        ("headers", "fragment"),
        [
            ({"Mcp-Method": "tools/call", "Mcp-Name": "x.y"}, "MCP-Protocol-Version"),
            ({"MCP-Protocol-Version": MODERN, "Mcp-Name": "x.y"}, "Mcp-Method is missing"),
            (
                {"MCP-Protocol-Version": MODERN, "Mcp-Method": "tools/list", "Mcp-Name": "x.y"},
                "Mcp-Method header value",
            ),
            ({"MCP-Protocol-Version": MODERN, "Mcp-Method": "tools/call"}, "Mcp-Name is missing"),
            (
                {"MCP-Protocol-Version": MODERN, "Mcp-Method": "tools/call", "Mcp-Name": "a.b"},
                "Mcp-Name header value 'a.b'",
            ),
            (
                {
                    "MCP-Protocol-Version": MODERN,
                    "Mcp-Method": "tools/call",
                    "Mcp-Name": "=?base64?!!!?=",
                },
                "not valid base64",
            ),
        ],
    )
    def test_each_failure_is_named(self, headers: dict[str, str], fragment: str) -> None:
        problem = modern_header_problem(headers, self._message("tools/call", name="x.y"))
        assert problem is not None and fragment in problem

    def test_a_method_with_no_name_needs_no_mcp_name(self) -> None:
        headers = {"MCP-Protocol-Version": MODERN, "Mcp-Method": "tools/list"}
        assert modern_header_problem(headers, self._message("tools/list")) is None

    @pytest.mark.parametrize(
        ("code", "status"),
        [(None, 200), (-32601, 404), (-32602, 400), (-32020, 400), (-32021, 400), (-32022, 400)],
    )
    def test_statuses(self, code: int | None, status: int) -> None:
        assert modern_status(code) == status


class TestOverHttp:
    def _client(self) -> Any:
        from fastapi.testclient import TestClient

        from metaforge.mcp.__main__ import build_http_app

        server = UnifiedMcpServer(adapters=[], caller=Caller.UNTRUSTED)
        return TestClient(build_http_app(server, enable_sse=False))

    def _post(self, client: Any, method: str, headers: dict[str, str], **params: Any) -> Any:
        body = {"jsonrpc": "2.0", "id": 3, "method": method, "params": {**params, "_meta": _meta()}}
        return client.post("/mcp", json=body, headers=headers)

    def _headers(self, method: str, **extra: str) -> dict[str, str]:
        return {"MCP-Protocol-Version": MODERN, "Mcp-Method": method, **extra}

    def test_a_well_formed_request_is_served_with_no_session(self) -> None:
        response = self._post(self._client(), "tools/list", self._headers("tools/list"))
        assert response.status_code == 200
        assert response.json()["result"]["resultType"] == "complete"
        assert "Mcp-Session-Id" not in response.headers

    def test_discover_over_http(self) -> None:
        response = self._post(self._client(), "server/discover", self._headers("server/discover"))
        assert response.status_code == 200
        assert response.json()["result"]["supportedVersions"][0] == MODERN

    def test_missing_method_header_is_a_400_header_mismatch(self) -> None:
        response = self._post(self._client(), "tools/list", {"MCP-Protocol-Version": MODERN})
        assert response.status_code == 400
        assert response.json()["error"]["code"] == HEADER_MISMATCH

    def test_an_unknown_method_is_a_404(self) -> None:
        response = self._post(self._client(), "ping", self._headers("ping"))
        assert response.status_code == 404
        assert response.json()["error"]["code"] == -32601

    def test_malformed_meta_is_a_400(self) -> None:
        body = {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/list",
            "params": {"_meta": {META_PROTOCOL_VERSION: MODERN}},
        }
        response = self._client().post("/mcp", json=body, headers=self._headers("tools/list"))
        assert response.status_code == 400
        assert response.json()["error"]["code"] == INVALID_PARAMS

    def test_a_handshake_client_is_unaffected(self) -> None:
        client = self._client()
        init = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-11-25", "capabilities": {}},
            },
        )
        assert init.status_code == 200
        assert "resultType" not in init.json()["result"]
        listed = client.post(
            "/mcp",
            headers={"Mcp-Session-Id": init.headers["Mcp-Session-Id"]},
            json={"jsonrpc": "2.0", "id": 2, "method": "ping", "params": {}},
        )
        # A legacy unknown method keeps its old 200 + JSON-RPC error.
        assert listed.status_code == 200


class TestHealthCheckWithoutAHandshake:
    """FORGE-696: a 2026-07-28 client never sends ``initialize``.

    health.check read only the handshake copy, so Claude Code 2.1.294 was told
    "no initialize handshake has been completed on this server" and shown the
    server's default revision as negotiated, while every call it made worked.
    """

    def _client(self, server: UnifiedMcpServer) -> dict[str, Any]:
        response = _call(
            server,
            "tools/call",
            {"name": "health.check", "arguments": {}, "_meta": _meta()},
        )
        text = response["result"]["content"][0]["text"]
        return dict(json.loads(text)["data"]["client"])

    def test_the_request_names_the_client_and_its_revision(self) -> None:
        client = self._client(UnifiedMcpServer(adapters=[]))
        assert client["connected"] is True
        assert (client["name"], client["version"]) == ("modern-client", "1")
        assert client["protocol_requested"] == client["protocol_negotiated"] == MODERN
        assert "handshake-free" in client["handshake"]
        assert "protocol_skew" not in client

    def test_an_earlier_handshake_client_does_not_answer_for_it(self) -> None:
        """The handshake copy belongs to whichever legacy client came last."""
        server = UnifiedMcpServer(adapters=[])
        _call(
            server,
            "initialize",
            {"protocolVersion": "2025-06-18", "clientInfo": {"name": "old-harness"}},
        )
        client = self._client(server)
        assert client["name"] == "modern-client"
        assert client["protocol_negotiated"] == MODERN

    def test_the_handshake_path_still_reports_its_own(self) -> None:
        server = UnifiedMcpServer(adapters=[])
        _call(
            server,
            "initialize",
            {"protocolVersion": "2025-06-18", "clientInfo": {"name": "old-harness"}},
        )
        report = asyncio.run(server._health_check())["client"]
        assert report["name"] == "old-harness"
        assert report["protocol_negotiated"] == "2025-06-18"
        assert current_modern_request() is None
