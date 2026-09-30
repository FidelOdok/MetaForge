"""A verified token decides who the caller is, not a header (FORGE-330).

Over HTTP the server took `actor_id` from `X-MetaForge-Actor` — a header the
client writes — while the actor bound to the OAuth token was discarded:
`validate_token` returns it and the call site tested it for truthiness.

A remote caller could therefore claim to be anyone, and that claim is what
landed in the session record and the capture timeline. Self-asserted
attribution is not an audit trail, and it fails in the direction where the
record looks complete.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from mcp_core.context import current_context
from metaforge.mcp.__main__ import build_http_app
from metaforge.mcp.server import UnifiedMcpServer
from tool_registry.mcp_server.handlers import ToolManifest
from tool_registry.mcp_server.server import McpToolServer


class _ActorSpy(McpToolServer):
    """Records the actor the context carried when the tool ran."""

    def __init__(self) -> None:
        super().__init__(adapter_id="twin", version="0.1.0")
        self.actors: list[str] = []
        self.register_tool(
            ToolManifest(
                tool_id="twin.get_node",
                adapter_id="twin",
                name="twin.get_node",
                description="stub",
                capability="test",
            ),
            self._ok,
        )

    async def _ok(self, args: dict[str, Any]) -> dict[str, Any]:
        self.actors.append(current_context().actor_id)
        return {"ok": True}


class _FakeOAuth:
    """Stands in for OAuthProvider: one live token bound to one actor."""

    class _Config:
        enabled = True
        # The 401 path builds a WWW-Authenticate pointing at the protected
        # resource metadata, and asks the provider for its issuer first.
        issuer = None
        # FORGE-330: whether this deployment's login proves *who* is
        # calling. False here, as for the real shared-secret login: the
        # token's actor outranks the header either way, which is what
        # these tests are about.
        verified_identity = False

    def __init__(self, token: str, actor: str) -> None:
        self.config = self._Config()
        self._token = token
        self._actor = actor

    def validate_token(self, token: str | None) -> str | None:
        return self._actor if token == self._token else None


def _call(client: TestClient, headers: dict[str, str]) -> Any:
    return client.post(
        "/mcp",
        content=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "twin.get_node", "arguments": {}},
            }
        ),
        headers=headers,
    )


@pytest.fixture
def spy() -> _ActorSpy:
    return _ActorSpy()


class TestTheTokenWins:
    def test_a_verified_actor_overrides_the_header(self, spy: _ActorSpy) -> None:
        # The bug: the header used to win, so this recorded "user:impostor".
        app = build_http_app(
            UnifiedMcpServer(adapters=[spy]),
            enable_sse=False,
            oauth=_FakeOAuth("tok-abc", "user:real"),
        )
        with TestClient(app) as client:
            response = _call(
                client,
                {
                    "Authorization": "Bearer tok-abc",
                    "X-MetaForge-Actor": "user:impostor",
                },
            )
        assert response.status_code == 200, response.text
        assert spy.actors == ["user:real"]

    def test_the_header_still_works_without_a_verified_token(self, spy: _ActorSpy) -> None:
        # Open mode, no OAuth configured: the header is the only signal there
        # is, and local use depends on it.
        app = build_http_app(UnifiedMcpServer(adapters=[spy]), enable_sse=False)
        with TestClient(app) as client:
            response = _call(client, {"X-MetaForge-Actor": "agent:claude_code"})
        assert response.status_code == 200, response.text
        assert spy.actors == ["agent:claude_code"]

    def test_a_shared_api_key_identifies_nobody(self, spy: _ActorSpy) -> None:
        # Authorised is not the same as attributable. A static key says the
        # caller may act; it says nothing about who they are, so it must not
        # manufacture an identity — and must not silently bless the header
        # as one either.
        app = build_http_app(
            UnifiedMcpServer(adapters=[spy]), enable_sse=False, api_key="shared-secret"
        )
        with TestClient(app) as client:
            response = _call(
                client,
                {
                    "Authorization": "Bearer shared-secret",
                    "X-MetaForge-Actor": "user:claimed",
                },
            )
        assert response.status_code == 200, response.text
        # The header is still what it always was: an unverified claim. What
        # matters is that the key did not turn it into a verified one.
        assert spy.actors == ["user:claimed"]

    def test_an_invalid_token_is_refused_before_any_actor_question(self, spy: _ActorSpy) -> None:
        app = build_http_app(
            UnifiedMcpServer(adapters=[spy]),
            enable_sse=False,
            oauth=_FakeOAuth("tok-abc", "user:real"),
        )
        with TestClient(app) as client:
            response = _call(
                client,
                {"Authorization": "Bearer wrong", "X-MetaForge-Actor": "user:impostor"},
            )
        assert response.status_code == 401
        assert spy.actors == [], "the tool ran despite a rejected token"
