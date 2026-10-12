"""subscriptions/listen for 2026-07-28 clients (FORGE-603).

Nothing this server offers changes while it is up, so it honours no
notification type: it acknowledges with an empty filter and then closes the
subscription gracefully.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from mcp_core.guardrails import Caller
from mcp_core.protocol_versions import (
    META_CLIENT_CAPABILITIES,
    META_PROTOCOL_VERSION,
    META_SUBSCRIPTION_ID,
    MODERN_PREVIEW_ENV,
    is_subscription_listen,
    subscription_ack,
)
from metaforge.mcp.server import UnifiedMcpServer


@pytest.fixture(autouse=True)
def _preview(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MODERN_PREVIEW_ENV, "on")


def _listen(rid: int = 5, notifications: Any = None) -> dict[str, Any]:
    params: dict[str, Any] = {
        "_meta": {META_PROTOCOL_VERSION: "2026-07-28", META_CLIENT_CAPABILITIES: {}}
    }
    if notifications is not None:
        params["notifications"] = notifications
    return {"jsonrpc": "2.0", "id": rid, "method": "subscriptions/listen", "params": params}


def _send(message: dict[str, Any]) -> dict[str, Any]:
    server = UnifiedMcpServer(adapters=[], caller=Caller.UNTRUSTED)
    return dict(json.loads(asyncio.run(server.handle_request(json.dumps(message)))))


def test_the_subscription_closes_gracefully_with_its_id() -> None:
    result = _send(_listen(notifications={"toolsListChanged": True}))["result"]
    assert result["resultType"] == "complete"
    assert result["_meta"][META_SUBSCRIPTION_ID] == 5


def test_the_acknowledgement_honours_nothing() -> None:
    ack = subscription_ack(5)
    assert ack["method"] == "notifications/subscriptions/acknowledged"
    assert ack["params"]["_meta"][META_SUBSCRIPTION_ID] == 5
    assert ack["params"]["notifications"] == {}
    assert "id" not in ack


@pytest.mark.parametrize(
    "notifications",
    ["all", {"toolsListChanged": "yes"}, {"resourceSubscriptions": "file:///x"}],
)
def test_a_malformed_filter_is_refused(notifications: Any) -> None:
    assert _send(_listen(notifications=notifications))["error"]["code"] == -32602


def test_only_a_handshake_free_listen_is_recognised() -> None:
    assert is_subscription_listen(_listen())
    legacy = {"jsonrpc": "2.0", "id": 1, "method": "subscriptions/listen", "params": {}}
    assert not is_subscription_listen(legacy)
    assert _send(legacy)["error"]["code"] == -32601


def test_over_http_the_ack_comes_first_then_the_close() -> None:
    from fastapi.testclient import TestClient

    from metaforge.mcp.__main__ import build_http_app

    server = UnifiedMcpServer(adapters=[], caller=Caller.UNTRUSTED)
    client = TestClient(build_http_app(server, enable_sse=False))
    response = client.post(
        "/mcp",
        json=_listen(rid=9, notifications={"toolsListChanged": True}),
        headers={"MCP-Protocol-Version": "2026-07-28", "Mcp-Method": "subscriptions/listen"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = [
        json.loads(line[len("data: ") :])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert [e.get("method") for e in events] == ["notifications/subscriptions/acknowledged", None]
    assert events[1]["id"] == 9
    assert events[1]["result"]["_meta"][META_SUBSCRIPTION_ID] == 9
