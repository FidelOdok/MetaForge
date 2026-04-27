"""Unit tests for MCP-transport-level auth wiring (MET-338).

Covers both server-side enforcement (the HTTP FastAPI app from
``metaforge.mcp.__main__.build_http_app`` and the stdio
``_stdio_auth_check``) and client-side header propagation
(``HttpTransport`` + ``StdioTransport``).
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from metaforge.mcp.__main__ import (
    _stdio_auth_check,
    build_http_app,
)
from metaforge.mcp.server import UnifiedMcpServer
from tool_registry.mcp_server.handlers import ToolManifest
from tool_registry.mcp_server.server import McpToolServer


class _StubAdapter(McpToolServer):
    def __init__(self) -> None:
        super().__init__(adapter_id="stub", version="0.1.0")
        self.register_tool(
            ToolManifest(
                tool_id="stub.echo",
                adapter_id="stub",
                name="echo",
                description="",
                capability="test",
            ),
            self._echo,
        )

    async def _echo(self, args: dict[str, Any]) -> dict[str, Any]:
        return {"echo": args}


@pytest.fixture
def server() -> UnifiedMcpServer:
    return UnifiedMcpServer(adapters=[_StubAdapter()])


# ---------------------------------------------------------------------------
# HTTP server-side auth
# ---------------------------------------------------------------------------


class TestHttpAuth:
    def test_open_mode_no_header_required(self, server: UnifiedMcpServer) -> None:
        app = build_http_app(server, enable_sse=False, api_key=None)
        client = TestClient(app)
        resp = client.post(
            "/mcp",
            content='{"jsonrpc":"2.0","id":"1","method":"tool/list","params":{}}',
        )
        assert resp.status_code == 200
        assert "tools" in resp.json()["result"]

    def test_missing_header_rejected(self, server: UnifiedMcpServer) -> None:
        app = build_http_app(server, enable_sse=False, api_key="secret-123")
        client = TestClient(app)
        resp = client.post(
            "/mcp",
            content='{"jsonrpc":"2.0","id":"1","method":"tool/list","params":{}}',
        )
        assert resp.status_code == 401
        assert resp.json()["detail"]["error_type"] == "auth_error"
        assert resp.json()["detail"]["reason"] == "missing_key"

    def test_wrong_bearer_rejected(self, server: UnifiedMcpServer) -> None:
        app = build_http_app(server, enable_sse=False, api_key="secret-123")
        client = TestClient(app)
        resp = client.post(
            "/mcp",
            content='{"jsonrpc":"2.0","id":"1","method":"tool/list","params":{}}',
            headers={"Authorization": "Bearer wrong-key"},
        )
        assert resp.status_code == 401
        assert resp.json()["detail"]["reason"] == "mismatch"

    def test_correct_bearer_accepted(self, server: UnifiedMcpServer) -> None:
        app = build_http_app(server, enable_sse=False, api_key="secret-123")
        client = TestClient(app)
        resp = client.post(
            "/mcp",
            content='{"jsonrpc":"2.0","id":"1","method":"tool/list","params":{}}',
            headers={"Authorization": "Bearer secret-123"},
        )
        assert resp.status_code == 200

    def test_health_endpoint_remains_open(self, server: UnifiedMcpServer) -> None:
        app = build_http_app(server, enable_sse=False, api_key="secret-123")
        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["service"] == "metaforge-mcp"

    def test_non_bearer_scheme_rejected(self, server: UnifiedMcpServer) -> None:
        app = build_http_app(server, enable_sse=False, api_key="secret-123")
        client = TestClient(app)
        resp = client.post(
            "/mcp",
            content='{"jsonrpc":"2.0","id":"1","method":"tool/list","params":{}}',
            headers={"Authorization": "Basic c2VjcmV0LTEyMw=="},
        )
        assert resp.status_code == 401
        assert resp.json()["detail"]["reason"] == "missing_key"


# ---------------------------------------------------------------------------
# Stdio server-side auth check
# ---------------------------------------------------------------------------


class TestStdioAuthCheck:
    def test_open_mode_when_api_key_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("METAFORGE_MCP_API_KEY", raising=False)
        ok, reason = _stdio_auth_check()
        assert ok is True
        assert reason == "open_mode"

    def test_missing_client_key_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("METAFORGE_MCP_API_KEY", "secret-123")
        monkeypatch.delenv("METAFORGE_MCP_CLIENT_KEY", raising=False)
        ok, reason = _stdio_auth_check()
        assert ok is False
        assert reason == "missing_key"

    def test_wrong_client_key_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("METAFORGE_MCP_API_KEY", "secret-123")
        monkeypatch.setenv("METAFORGE_MCP_CLIENT_KEY", "wrong")
        ok, reason = _stdio_auth_check()
        assert ok is False
        assert reason == "mismatch"

    def test_correct_client_key_accepted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("METAFORGE_MCP_API_KEY", "secret-123")
        monkeypatch.setenv("METAFORGE_MCP_CLIENT_KEY", "secret-123")
        ok, reason = _stdio_auth_check()
        assert ok is True
        assert reason == "match"


# ---------------------------------------------------------------------------
# Client-side header propagation
# ---------------------------------------------------------------------------


class TestHttpTransportHeader:
    @pytest.mark.asyncio
    async def test_attaches_bearer_when_api_key_set(self) -> None:
        from mcp_core.transports import HttpTransport

        transport = HttpTransport("http://example.invalid", api_key="secret-123")
        captured: dict[str, Any] = {}

        class _FakeResp:
            async def __aenter__(self) -> _FakeResp:
                return self

            async def __aexit__(self, *args: Any) -> None:
                pass

            async def text(self) -> str:
                return '{"ok":true}'

        class _FakeSession:
            def post(self, url: str, **kwargs: Any) -> _FakeResp:
                captured["url"] = url
                captured["headers"] = kwargs.get("headers")
                return _FakeResp()

            async def close(self) -> None:
                pass

        transport._session = _FakeSession()  # type: ignore[assignment]
        await transport.send('{"x":1}')
        assert captured["headers"]["Authorization"] == "Bearer secret-123"

    @pytest.mark.asyncio
    async def test_no_bearer_when_api_key_absent(self) -> None:
        from mcp_core.transports import HttpTransport

        transport = HttpTransport("http://example.invalid")
        captured: dict[str, Any] = {}

        class _FakeResp:
            async def __aenter__(self) -> _FakeResp:
                return self

            async def __aexit__(self, *args: Any) -> None:
                pass

            async def text(self) -> str:
                return '{"ok":true}'

        class _FakeSession:
            def post(self, url: str, **kwargs: Any) -> _FakeResp:
                captured["headers"] = kwargs.get("headers")
                return _FakeResp()

            async def close(self) -> None:
                pass

        transport._session = _FakeSession()  # type: ignore[assignment]
        await transport.send('{"x":1}')
        assert "Authorization" not in (captured["headers"] or {})


class TestStdioTransportEnv:
    def test_api_key_added_to_subprocess_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from mcp_core.transports import StdioTransport

        monkeypatch.setenv("PRESERVE_ME", "yes")
        transport = StdioTransport(command=["/bin/true"], api_key="secret-123")
        assert transport._env["METAFORGE_MCP_CLIENT_KEY"] == "secret-123"
        # Inherited environment is kept intact.
        assert transport._env.get("PRESERVE_ME") == "yes"

    def test_no_env_modification_when_api_key_absent(self) -> None:
        from mcp_core.transports import StdioTransport

        transport = StdioTransport(command=["/bin/true"])
        # Without ``api_key``, env stays as-is (``None`` → inherit).
        assert transport._env is None
