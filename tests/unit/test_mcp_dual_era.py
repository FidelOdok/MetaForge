"""Legacy and handshake-free requests side by side (FORGE-599).

The 2026-07-28 revision drops ``initialize``: each request names its own
revision in ``params._meta``. This server answers a revision it does not serve
with the spec's ``UnsupportedProtocolVersionError``, keeps serving every
handshake client exactly as before, and now negotiates 2025-11-25.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from mcp_core.elicitation import declares_form_elicitation
from mcp_core.guardrails import Caller
from mcp_core.protocol_versions import (
    HEADER_MISMATCH,
    LEGACY_PROTOCOL_VERSIONS,
    META_PROTOCOL_VERSION,
    MODERN_PROTOCOL_VERSIONS,
    SUPPORTED_PROTOCOL_VERSIONS,
    UNSUPPORTED_PROTOCOL_VERSION,
    header_mismatch,
    request_protocol_version,
)
from metaforge.mcp.server import UnifiedMcpServer


def _meta(version: str) -> dict[str, Any]:
    return {
        META_PROTOCOL_VERSION: version,
        "io.modelcontextprotocol/clientInfo": {"name": "modern", "version": "1"},
        "io.modelcontextprotocol/clientCapabilities": {},
    }


def _call(server: UnifiedMcpServer, method: str, params: dict[str, Any]) -> dict[str, Any]:
    raw = asyncio.run(
        server.handle_request(
            json.dumps({"jsonrpc": "2.0", "id": 7, "method": method, "params": params})
        )
    )
    return dict(json.loads(raw))


class TestTheSupportedSets:
    def test_claude_codes_revision_is_now_negotiated(self) -> None:
        assert "2025-11-25" in LEGACY_PROTOCOL_VERSIONS
        server = UnifiedMcpServer(adapters=[])
        result = _call(server, "initialize", {"protocolVersion": "2025-11-25", "capabilities": {}})
        assert result["result"]["protocolVersion"] == "2025-11-25"

    def test_the_modern_revision_is_not_claimed_before_it_is_served(self) -> None:
        # resultType/server-discover (FORGE-600) and MRTR (FORGE-601) first.
        assert "2026-07-28" not in MODERN_PROTOCOL_VERSIONS
        assert "2026-07-28" not in SUPPORTED_PROTOCOL_VERSIONS


class TestAHandshakeFreeRequest:
    def test_an_unserved_revision_gets_the_specs_error(self) -> None:
        response = _call(
            UnifiedMcpServer(adapters=[]), "tools/list", {"_meta": _meta("2026-07-28")}
        )
        error = response["error"]
        assert error["code"] == UNSUPPORTED_PROTOCOL_VERSION == -32022
        assert error["data"]["requested"] == "2026-07-28"
        # Newest first, so a client can take the head of the list.
        assert error["data"]["supported"] == sorted(SUPPORTED_PROTOCOL_VERSIONS, reverse=True)
        assert response["id"] == 7

    def test_a_revision_we_speak_named_in_meta_is_served(self) -> None:
        response = _call(
            UnifiedMcpServer(adapters=[]), "tools/list", {"_meta": _meta("2025-11-25")}
        )
        assert "result" in response

    def test_a_request_with_no_version_is_served_as_before(self) -> None:
        assert "result" in _call(UnifiedMcpServer(adapters=[]), "tools/list", {})

    def test_initialize_negotiates_whatever_its_meta_says(self) -> None:
        params = {"protocolVersion": "2025-06-18", "capabilities": {}, "_meta": _meta("1900-01-01")}
        response = _call(UnifiedMcpServer(adapters=[]), "initialize", params)
        assert response["result"]["protocolVersion"] == "2025-06-18"

    @pytest.mark.parametrize("params", [None, [], {"_meta": None}, {"_meta": {}}])
    def test_odd_params_name_no_version(self, params: Any) -> None:
        assert request_protocol_version(params) is None


class TestTheVersionHeader:
    def test_a_disagreeing_header_is_named(self) -> None:
        reason = header_mismatch("2025-06-18", {"_meta": _meta("2026-07-28")})
        assert reason is not None
        assert "2025-06-18" in reason and "2026-07-28" in reason

    @pytest.mark.parametrize(
        ("header", "params"),
        [
            ("2026-07-28", {"_meta": _meta("2026-07-28")}),
            # A 2025-06-18 client sends the header and no _meta version.
            ("2025-06-18", {}),
            (None, {"_meta": _meta("2026-07-28")}),
        ],
    )
    def test_agreement_or_absence_is_not_a_mismatch(self, header: str | None, params: Any) -> None:
        assert header_mismatch(header, params) is None


class TestFormElicitation:
    @pytest.mark.parametrize(
        ("caps", "expected"),
        [
            ({"elicitation": {}}, True),
            ({"elicitation": {"form": {}}}, True),
            ({"elicitation": {"form": {}, "url": {}}}, True),
            ({"elicitation": {"url": {}}}, False),
            ({}, False),
            (None, False),
        ],
    )
    def test_only_a_client_that_takes_forms_is_sent_one(self, caps: Any, expected: bool) -> None:
        assert declares_form_elicitation(caps) is expected

    def test_a_url_only_client_is_not_asked_inline(self) -> None:
        server = UnifiedMcpServer(adapters=[], caller=Caller.UNTRUSTED)
        _call(
            server,
            "initialize",
            {"protocolVersion": "2025-11-25", "capabilities": {"elicitation": {"url": {}}}},
        )
        assert server.can_elicit is False


class TestOverHttp:
    def _client(self) -> Any:
        from fastapi.testclient import TestClient

        from metaforge.mcp.__main__ import build_http_app

        server = UnifiedMcpServer(adapters=[], caller=Caller.UNTRUSTED)
        return TestClient(build_http_app(server, enable_sse=False))

    def test_an_unserved_revision_is_a_400_with_the_error_body(self) -> None:
        response = self._client().post(
            "/mcp",
            headers={"MCP-Protocol-Version": "2026-07-28"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {"_meta": _meta("2026-07-28")},
            },
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == UNSUPPORTED_PROTOCOL_VERSION

    def test_a_header_that_disagrees_with_the_body_is_a_400(self) -> None:
        response = self._client().post(
            "/mcp",
            headers={"MCP-Protocol-Version": "2025-06-18"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {"_meta": _meta("2026-07-28")},
            },
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == HEADER_MISMATCH
        assert response.json()["id"] == 1

    def test_a_legacy_client_is_unchanged(self) -> None:
        client = self._client()
        init = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {}},
            },
        )
        assert init.status_code == 200
        listed = client.post(
            "/mcp",
            headers={
                "MCP-Protocol-Version": "2025-06-18",
                "Mcp-Session-Id": init.headers["Mcp-Session-Id"],
            },
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        assert listed.status_code == 200
        assert "result" in listed.json()
