"""A held write answered inline, over HTTP, against a real server (FORGE-423).

This is FORGE-416's acceptance criterion, which that ticket could not meet:
`can_elicit: true` and a held write answered inside the client rather than
parked in the dashboard queue. FORGE-416 fixed the protocol revision and was
necessary but not sufficient -- `attach_elicitor` ran only on the stdio path,
so no HTTP client could be asked at all.

Driven through uvicorn rather than an ASGI transport on purpose. The thing
being proved is that the server-to-client direction works over a real
connection: a long-lived SSE stream the server pushes a request down, and a
separate POST carrying the answer back. An in-process transport would prove
the handlers compose, which the unit tests already do.
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from typing import Any

import httpx
import pytest
import uvicorn

from mcp_core.elicitation import ELICITATION_PROTOCOL_VERSION
from mcp_core.guardrails import Caller
from metaforge.mcp.__main__ import build_http_app
from metaforge.mcp.server import UnifiedMcpServer
from tool_registry.mcp_server.handlers import ToolManifest
from tool_registry.mcp_server.server import McpToolServer

pytestmark = pytest.mark.asyncio

_HEADERS = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}


class _Twin(McpToolServer):
    def __init__(self) -> None:
        super().__init__(adapter_id="twin", version="0.1.0")
        for tool_id in ("twin.record_decision", "twin.attempt_promotion"):
            self.register_tool(
                ToolManifest(
                    tool_id=tool_id,
                    adapter_id="twin",
                    name=tool_id,
                    description="stub",
                    capability="test",
                ),
                self._handler,
            )

    async def _handler(self, _args: dict[str, Any]) -> dict[str, Any]:
        return {"node_id": "written"}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="module")
def base_url() -> Any:
    port = _free_port()
    app = build_http_app(UnifiedMcpServer([_Twin()], caller=Caller.UNTRUSTED), enable_sse=False)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):  # up to ~10s for the port to answer
        try:
            httpx.get(f"{url}/health", timeout=1.0)
            break
        except httpx.HTTPError:
            time.sleep(0.1)
    else:  # pragma: no cover — the server never came up
        pytest.skip("uvicorn did not start")
    yield url
    server.should_exit = True


async def _initialize(client: httpx.AsyncClient, url: str) -> str:
    response = await client.post(
        f"{url}/mcp",
        headers=_HEADERS,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {"elicitation": {}},
                "clientInfo": {"name": "claude-code", "version": "2.1.286"},
            },
        },
    )
    # FORGE-416: a client ahead of us is answered with our newest revision.
    assert response.json()["result"]["protocolVersion"] == ELICITATION_PROTOCOL_VERSION
    return str(response.headers["Mcp-Session-Id"])


class _Client:
    """A client that reads the stream and answers whatever is asked."""

    def __init__(self, url: str, session: str, answer: dict[str, Any]) -> None:
        self._url = url
        self._session = session
        self._answer = answer
        self.asked: dict[str, Any] | None = None
        self.answered = asyncio.Event()

    async def run(self) -> None:
        headers = {**_HEADERS, "Mcp-Session-Id": self._session}
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("GET", f"{self._url}/mcp", headers=headers) as stream:
                async for line in stream.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    message = json.loads(line[6:])
                    if message.get("method") != "elicitation/create":
                        continue
                    self.asked = message
                    await client.post(
                        f"{self._url}/mcp",
                        headers=headers,
                        json={"jsonrpc": "2.0", "id": message["id"], "result": self._answer},
                    )
                    self.answered.set()
                    return


async def _call(client: httpx.AsyncClient, url: str, session: str, tool: str) -> dict[str, Any]:
    response = await client.post(
        f"{url}/mcp",
        headers={**_HEADERS, "Mcp-Session-Id": session},
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": tool, "arguments": {"title": "inline"}},
        },
    )
    return dict(response.json())


async def _with_listener(
    url: str, answer: dict[str, Any], tool: str = "twin.record_decision"
) -> tuple[dict[str, Any], _Client]:
    async with httpx.AsyncClient(timeout=30) as client:
        session = await _initialize(client, url)
        listener = _Client(url, session, answer)
        task = asyncio.create_task(listener.run())
        await asyncio.sleep(0.4)  # let the stream register
        body = await _call(client, url, session, tool)
        await asyncio.wait_for(listener.answered.wait(), timeout=15)
        task.cancel()
        return body, listener


class TestTheAcceptanceCriterion:
    async def test_can_elicit_is_true_once_the_stream_is_open(self, base_url: str) -> None:
        async with httpx.AsyncClient(timeout=30) as client:
            session = await _initialize(client, base_url)
            listener = _Client(base_url, session, {"action": "decline"})
            task = asyncio.create_task(listener.run())
            await asyncio.sleep(0.4)
            health = await client.post(
                f"{base_url}/mcp",
                headers={**_HEADERS, "Mcp-Session-Id": session},
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "health.check", "arguments": {}},
                },
            )
            body = json.loads(health.json()["result"]["content"][0]["text"])
            body = body.get("data") or body
            assert body["client"]["can_elicit"] is True
            task.cancel()

    async def test_a_held_write_is_asked_inline_and_runs_when_approved(self, base_url: str) -> None:
        """The whole ticket. Before this, the same call went to the dashboard
        queue and the client was never asked anything."""
        body, listener = await _with_listener(
            base_url, {"action": "accept", "content": {"approve": True}}
        )
        assert listener.asked is not None, "the client was never asked"
        assert (
            "MetaForge wants to run twin.record_decision" in (listener.asked["params"]["message"])
        )
        assert "error" not in body, body
        result = body["result"]
        assert result["isError"] is False
        # FORGE-417 rides along: the result says it was held, and by which route.
        approval = result["_meta"]["approval"]
        assert approval["held"] is True
        assert approval["outcome"] == "approved"
        assert approval["route"] == "elicitation"
        assert "held for human approval" in result["content"][1]["text"]

    async def test_a_refusal_inside_the_client_stops_the_write(self, base_url: str) -> None:
        body, listener = await _with_listener(
            base_url, {"action": "accept", "content": {"approve": False}}
        )
        assert listener.asked is not None
        assert "error" in body, body
        assert "rejected" in json.dumps(body["error"])

    async def test_declining_the_prompt_also_stops_it(self, base_url: str) -> None:
        body, _ = await _with_listener(base_url, {"action": "decline"})
        assert "error" in body
        assert "rejected" in json.dumps(body["error"])


class TestWhatInlineApprovalCannotDo:
    async def test_a_human_authority_tool_still_refuses(self, base_url: str) -> None:
        """Deliberate, and worth pinning so it reads as a decision.

        FORGE-393 requires that a tool whose whole result is "a named human
        decided this" records who. An elicitation answer carries no identity
        -- the client can say a user clicked yes, not which user -- so the
        gate names nobody and the call is refused rather than recorded
        against an authority that does not exist.
        """
        body, listener = await _with_listener(
            base_url,
            {"action": "accept", "content": {"approve": True}},
            tool="twin.attempt_promotion",
        )
        assert listener.asked is not None, "it should still ask"
        assert "error" in body, body
        assert "did not identify who granted it" in json.dumps(body["error"])
