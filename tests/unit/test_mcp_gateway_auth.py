"""The MCP sidecar authenticates its calls to the gateway.

With an auth provider on the gateway, a sidecar call carrying no bearer token
is refused (401). On a hosted stack that refused every write: the approval
ledger could not record the hold. ``METAFORGE_GATEWAY_API_KEY`` is sent on
every sidecar-to-gateway client; unset (local installs), nothing is added.
"""

from __future__ import annotations

import httpx
import pytest

from metaforge.mcp import remote_flows
from metaforge.mcp.gateway_auth import GATEWAY_KEY_ENV, gateway_auth_headers


def test_no_key_no_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(GATEWAY_KEY_ENV, raising=False)
    assert gateway_auth_headers() == {}


def test_key_becomes_a_bearer_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(GATEWAY_KEY_ENV, "  svc-token-123  ")
    assert gateway_auth_headers() == {"Authorization": "Bearer svc-token-123"}


@pytest.mark.parametrize("key", ["svc-token-123", ""])
async def test_gateway_client_sends_it(monkeypatch: pytest.MonkeyPatch, key: str) -> None:
    if key:
        monkeypatch.setenv(GATEWAY_KEY_ENV, key)
    else:
        monkeypatch.delenv(GATEWAY_KEY_ENV, raising=False)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    real = httpx.AsyncClient

    def client_with_transport(*args: object, **kwargs: object) -> httpx.AsyncClient:
        return real(*args, transport=httpx.MockTransport(handler), **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(remote_flows.httpx, "AsyncClient", client_with_transport)
    gateway = remote_flows._Gateway("http://gateway:8000", None, 5.0)
    await gateway.request("GET", "/v1/approvals")
    assert seen, "no request was made"
    if key:
        assert seen[0].headers["Authorization"] == f"Bearer {key}"
    else:
        assert "Authorization" not in seen[0].headers
