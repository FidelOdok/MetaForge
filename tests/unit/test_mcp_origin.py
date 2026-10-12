"""The MCP endpoint refuses browser origins it does not know (FORGE-604)."""

from __future__ import annotations

from typing import Any

import pytest

from mcp_core.guardrails import Caller
from metaforge.mcp.origin import OriginPolicy, normalise_origin
from metaforge.mcp.server import UnifiedMcpServer


def _policy(**env: str) -> OriginPolicy:
    return OriginPolicy.from_env(env, issuer=env.pop("issuer", None))


class TestThePolicy:
    @pytest.mark.parametrize("origin", [None, "", "   "])
    def test_no_origin_is_not_a_browser_and_is_allowed(self, origin: str | None) -> None:
        assert _policy().allows(origin)

    @pytest.mark.parametrize(
        "origin",
        [
            "http://localhost:5173",
            "http://app.localhost",
            "http://127.0.0.1:8765",
            "http://127.1.2.3",
            "http://[::1]:3000",
        ],
    )
    def test_loopback_is_allowed(self, origin: str) -> None:
        assert _policy().allows(origin)

    @pytest.mark.parametrize("origin", ["https://claude.ai", "https://claude.com:443"])
    def test_the_hosted_claude_app_is_allowed(self, origin: str) -> None:
        assert _policy().allows(origin)

    @pytest.mark.parametrize(
        "origin",
        [
            # The rebinding page: its own name, never loopback.
            "http://evil.example",
            "http://evil.example:8765",
            "https://claude.ai.evil.example",
            "http://claude.ai",  # wrong scheme
            "null",
            "file://",
            "not a url",
        ],
    )
    def test_anything_else_is_refused(self, origin: str) -> None:
        assert not _policy().allows(origin)

    def test_the_servers_own_public_origin_is_allowed(self) -> None:
        policy = OriginPolicy.from_env({}, issuer="https://abc.trycloudflare.com/")
        assert policy.allows("https://abc.trycloudflare.com")

    def test_configured_origins_are_allowed_and_bad_entries_ignored(self) -> None:
        policy = _policy(METAFORGE_MCP_ALLOWED_ORIGINS="https://ui.example.com:8443, nonsense ,")
        assert policy.allows("https://ui.example.com:8443")
        assert not policy.allows("https://ui.example.com")

    def test_a_star_turns_the_check_off(self) -> None:
        assert _policy(METAFORGE_MCP_ALLOWED_ORIGINS="*").allows("http://evil.example")

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("HTTPS://Claude.AI:443", "https://claude.ai"),
            ("http://host:80", "http://host"),
            ("http://host:8080/path", "http://host:8080"),
            ("http://[::1]:3000", "http://[::1]:3000"),
            ("ftp://host", None),
            ("http://host:99999", None),
        ],
    )
    def test_normalise(self, raw: str, expected: str | None) -> None:
        assert normalise_origin(raw) == expected


class TestOverHttp:
    def _client(self, monkeypatch: pytest.MonkeyPatch, allowed: str = "") -> tuple[Any, list[int]]:
        from fastapi.testclient import TestClient

        from metaforge.mcp.__main__ import build_http_app

        monkeypatch.setenv("METAFORGE_MCP_ALLOWED_ORIGINS", allowed)
        rejected: list[int] = []

        class _Metrics:
            def record_mcp_origin_rejected(self) -> None:
                rejected.append(1)

            def __getattr__(self, _name: str) -> Any:
                return lambda *a, **k: None

        server = UnifiedMcpServer(adapters=[], caller=Caller.UNTRUSTED, metrics=_Metrics())
        return TestClient(build_http_app(server, enable_sse=False)), rejected

    def _list(self, client: Any, origin: str | None) -> Any:
        headers = {"Origin": origin} if origin else {}
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        return client.post("/mcp", json=body, headers=headers)

    def test_a_foreign_origin_gets_403_and_is_counted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, rejected = self._client(monkeypatch)
        response = self._list(client, "http://evil.example")
        assert response.status_code == 403
        assert response.json()["error"]["message"] == "Origin not allowed"
        assert "id" not in response.json()
        assert rejected == [1]

    def test_a_client_without_origin_is_unaffected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, rejected = self._client(monkeypatch)
        assert self._list(client, None).status_code == 200
        assert rejected == []

    def test_a_configured_front_end_gets_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, _ = self._client(monkeypatch, allowed="https://ui.example.com")
        assert self._list(client, "https://ui.example.com").status_code == 200

    def test_delete_and_get_are_covered_too(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, _ = self._client(monkeypatch)
        assert client.delete("/mcp", headers={"Origin": "http://evil.example"}).status_code == 403
        assert client.get("/mcp", headers={"Origin": "http://evil.example"}).status_code == 403

    def test_health_is_not_checked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, _ = self._client(monkeypatch)
        assert client.get("/health", headers={"Origin": "http://evil.example"}).status_code == 200
