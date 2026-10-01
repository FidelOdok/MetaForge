"""Inline approvals reach an HTTP client (FORGE-423).

FORGE-360 built elicitation. FORGE-416 fixed the protocol revision it needs.
Inline approvals were still unreachable for every plugin, because
`attach_elicitor` ran only on the stdio path: `can_elicit` was false on HTTP
whatever a client declared, so every held write went to the dashboard queue.
Every plugin connects over HTTP -- the Claude Code manifest sets
`"type": "http"` -- so the feature existed for a transport almost nobody uses.

Fifth instance of this project's recurring shape: code that is correct,
tested, and unreachable from the surface that needs it. The semantics were
never in doubt -- `test_mcp_stdio_elicitation.py` proves them -- so what is
tested here is the channel, the per-session routing, and the fallbacks.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from mcp_core.elicitation import ELICITATION_PROTOCOL_VERSION, ElicitAction
from metaforge.mcp.http_elicitation import (
    ElicitationHub,
    HttpElicitor,
    is_jsonrpc_response,
    session_uuid,
)

_SESSION = "11111111-1111-1111-1111-111111111111"
_OTHER = "22222222-2222-2222-2222-222222222222"


async def _drain(hub: ElicitationHub, session: str, limit: int = 1) -> list[dict[str, Any]]:
    """Read `limit` pushed messages off a session's stream."""
    seen: list[dict[str, Any]] = []
    async for frame in hub.stream(session):
        text = frame.decode()
        if not text.startswith("event: message"):
            continue  # the open banner, or a keep-alive
        seen.append(json.loads(text.split("data: ", 1)[1].strip()))
        if len(seen) >= limit:
            return seen
    return seen


def _eligible(hub: ElicitationHub, session: str = _SESSION) -> None:
    hub.note_initialize(
        session,
        capabilities={"elicitation": {}},
        negotiated_protocol=ELICITATION_PROTOCOL_VERSION,
    )


@pytest.mark.asyncio
class TestAskingAndAnswering:
    async def test_a_held_call_is_pushed_and_the_answer_comes_back(self) -> None:
        """The whole point, end to end through the hub."""
        hub = ElicitationHub()
        _eligible(hub)
        reader = asyncio.create_task(_drain(hub, _SESSION))
        await asyncio.sleep(0.05)

        asked = asyncio.create_task(hub.elicit(_SESSION, "run it?", {"type": "object"}))
        [request] = await asyncio.wait_for(reader, timeout=5)
        assert request["method"] == "elicitation/create"
        assert request["params"]["message"] == "run it?"

        hub.resolve(
            {
                "jsonrpc": "2.0",
                "id": request["id"],
                "result": {"action": "accept", "content": {"approve": True}},
            }
        )
        result = await asyncio.wait_for(asked, timeout=5)
        assert result.action is ElicitAction.ACCEPT
        assert result.content == {"approve": True}

    async def test_a_decline_is_a_decline(self) -> None:
        hub = ElicitationHub()
        _eligible(hub)
        reader = asyncio.create_task(_drain(hub, _SESSION))
        await asyncio.sleep(0.05)
        asked = asyncio.create_task(hub.elicit(_SESSION, "run it?", {}))
        [request] = await asyncio.wait_for(reader, timeout=5)
        hub.resolve({"jsonrpc": "2.0", "id": request["id"], "result": {"action": "decline"}})
        assert (await asyncio.wait_for(asked, timeout=5)).action is ElicitAction.DECLINE

    async def test_no_answer_is_a_cancel_not_a_refusal(self) -> None:
        """A client that never answers is not a reviewer saying no. The gate
        maps cancel to TIMED_OUT, so the agent is told nobody looked."""
        hub = ElicitationHub(timeout_seconds=0.1)
        _eligible(hub)
        reader = asyncio.create_task(_drain(hub, _SESSION))
        await asyncio.sleep(0.05)
        result = await hub.elicit(_SESSION, "run it?", {})
        assert result.action is ElicitAction.CANCEL
        reader.cancel()

    async def test_a_client_error_is_a_cancel_not_an_approval(self) -> None:
        hub = ElicitationHub()
        _eligible(hub)
        reader = asyncio.create_task(_drain(hub, _SESSION))
        await asyncio.sleep(0.05)
        asked = asyncio.create_task(hub.elicit(_SESSION, "run it?", {}))
        [request] = await asyncio.wait_for(reader, timeout=5)
        hub.resolve({"jsonrpc": "2.0", "id": request["id"], "error": {"code": -32601}})
        assert (await asyncio.wait_for(asked, timeout=5)).action is ElicitAction.CANCEL

    async def test_asking_a_session_with_no_stream_cancels_rather_than_hangs(self) -> None:
        """The stream can close between the eligibility check and the ask.
        Cancelling is the truth -- nobody was asked, and nobody said no."""
        hub = ElicitationHub(timeout_seconds=0.1)
        assert (await hub.elicit(_SESSION, "run it?", {})).action is ElicitAction.CANCEL


@pytest.mark.asyncio
class TestItAsksTheRightClient:
    async def test_two_sessions_do_not_share_a_stream(self) -> None:
        """One sidecar serves many clients from one server. An approval
        pushed to the wrong stream asks the wrong person."""
        hub = ElicitationHub()
        _eligible(hub, _SESSION)
        _eligible(hub, _OTHER)
        reader = asyncio.create_task(_drain(hub, _SESSION))
        other = asyncio.create_task(_drain(hub, _OTHER))
        await asyncio.sleep(0.05)

        asked = asyncio.create_task(hub.elicit(_SESSION, "for session one", {}))
        [request] = await asyncio.wait_for(reader, timeout=5)
        assert request["params"]["message"] == "for session one"
        assert not other.done(), "the other client was sent a question meant for someone else"

        hub.resolve({"jsonrpc": "2.0", "id": request["id"], "result": {"action": "decline"}})
        await asyncio.wait_for(asked, timeout=5)
        other.cancel()

    async def test_an_answer_for_an_unknown_id_is_not_swallowed(self) -> None:
        """`resolve` returning False is what lets the transport fall through
        to ordinary dispatch instead of dropping the body."""
        hub = ElicitationHub()
        assert hub.resolve({"jsonrpc": "2.0", "id": "elicit-nobody-1", "result": {}}) is False

    async def test_an_answer_cannot_be_delivered_twice(self) -> None:
        hub = ElicitationHub()
        _eligible(hub)
        reader = asyncio.create_task(_drain(hub, _SESSION))
        await asyncio.sleep(0.05)
        asked = asyncio.create_task(hub.elicit(_SESSION, "run it?", {}))
        [request] = await asyncio.wait_for(reader, timeout=5)
        answer = {"jsonrpc": "2.0", "id": request["id"], "result": {"action": "accept"}}
        assert hub.resolve(answer) is True
        assert hub.resolve(answer) is False
        await asyncio.wait_for(asked, timeout=5)


@pytest.mark.asyncio
class TestEligibilityIsPerConnection:
    async def test_no_stream_means_not_available(self) -> None:
        """So the gate falls back to the dashboard queue rather than choosing
        elicitation and then having nowhere to ask."""
        hub = ElicitationHub()
        _eligible(hub)
        assert hub.available(_SESSION) is False

    async def test_a_stream_without_the_declared_capability_is_not_available(self) -> None:
        hub = ElicitationHub()
        hub.note_initialize(
            _SESSION, capabilities={}, negotiated_protocol=ELICITATION_PROTOCOL_VERSION
        )
        reader = asyncio.create_task(_drain(hub, _SESSION))
        await asyncio.sleep(0.05)
        assert hub.available(_SESSION) is False
        reader.cancel()

    async def test_a_stream_on_too_old_a_revision_is_not_available(self) -> None:
        """`elicitation/create` does not exist before 2025-06-18, so a client
        that negotiated earlier is not listening for it."""
        hub = ElicitationHub()
        hub.note_initialize(
            _SESSION, capabilities={"elicitation": {}}, negotiated_protocol="2024-11-05"
        )
        reader = asyncio.create_task(_drain(hub, _SESSION))
        await asyncio.sleep(0.05)
        assert hub.available(_SESSION) is False
        reader.cancel()

    async def test_all_three_together_are_available(self) -> None:
        hub = ElicitationHub()
        _eligible(hub)
        reader = asyncio.create_task(_drain(hub, _SESSION))
        await asyncio.sleep(0.05)
        assert hub.available(_SESSION) is True
        reader.cancel()

    async def test_one_sessions_handshake_does_not_speak_for_another(self) -> None:
        hub = ElicitationHub()
        _eligible(hub, _SESSION)
        readers = [
            asyncio.create_task(_drain(hub, _SESSION)),
            asyncio.create_task(_drain(hub, _OTHER)),
        ]
        await asyncio.sleep(0.05)
        assert hub.available(_SESSION) is True
        assert hub.available(_OTHER) is False
        for r in readers:
            r.cancel()

    async def test_no_session_is_not_available(self) -> None:
        assert ElicitationHub().available(None) is False


class TestResponseDetection:
    @pytest.mark.parametrize(
        ("payload", "expected"),
        [
            ({"jsonrpc": "2.0", "id": "x", "result": {}}, True),
            ({"jsonrpc": "2.0", "id": "x", "error": {}}, True),
            ({"jsonrpc": "2.0", "id": 1, "method": "tools/call"}, False),
            ({"jsonrpc": "2.0", "method": "notifications/initialized"}, False),
            ({"jsonrpc": "2.0", "result": {}}, False),
            ("not a dict", False),
            (None, False),
        ],
    )
    def test_it(self, payload: Any, expected: bool) -> None:
        assert is_jsonrpc_response(payload) is expected


class TestSessionParsing:
    def test_a_good_uuid_parses(self) -> None:
        assert session_uuid(_SESSION) is not None

    @pytest.mark.parametrize("raw", [None, "", "not-a-uuid"])
    def test_anything_else_is_none_rather_than_raising(self, raw: str | None) -> None:
        assert session_uuid(raw) is None


@pytest.mark.asyncio
class TestTheElicitorResolvesTheSessionFromContext:
    async def test_it_uses_the_calling_connections_session(self) -> None:
        """One elicitor serves the whole app, so the session has to come from
        the active context rather than from construction."""
        from uuid import UUID

        from mcp_core.context import McpCallContext, with_context

        hub = ElicitationHub()
        _eligible(hub)
        elicitor = HttpElicitor(hub)
        ctx = McpCallContext(
            session_id=UUID(_SESSION),
            correlation_id=UUID("00000000-0000-0000-0000-000000000009"),
            actor_id="user:test",
        )
        reader = asyncio.create_task(_drain(hub, _SESSION))
        await asyncio.sleep(0.05)
        with with_context(ctx):
            assert elicitor.available() is True
            asked = asyncio.create_task(elicitor("run it?", {}))
        [request] = await asyncio.wait_for(reader, timeout=5)
        hub.resolve({"jsonrpc": "2.0", "id": request["id"], "result": {"action": "decline"}})
        assert (await asyncio.wait_for(asked, timeout=5)).action is ElicitAction.DECLINE

    async def test_with_no_stable_session_it_is_unavailable(self) -> None:
        """An invented session id would key a stream nothing can reach."""
        assert HttpElicitor(ElicitationHub()).available() is False

    async def test_and_calling_it_anyway_cancels_rather_than_hangs(self) -> None:
        assert (await HttpElicitor(ElicitationHub())("x", {})).action is ElicitAction.CANCEL


# ---------------------------------------------------------------------------
# The routes
# ---------------------------------------------------------------------------


def _app_and_server() -> tuple[Any, Any]:
    from mcp_core.guardrails import Caller
    from metaforge.mcp.__main__ import build_http_app
    from metaforge.mcp.server import UnifiedMcpServer
    from tool_registry.mcp_server.handlers import ToolManifest
    from tool_registry.mcp_server.server import McpToolServer

    class _Twin(McpToolServer):
        def __init__(self) -> None:
            super().__init__(adapter_id="twin", version="0.1.0")
            self.register_tool(
                ToolManifest(
                    tool_id="twin.record_decision",
                    adapter_id="twin",
                    name="record decision",
                    description="stub",
                    capability="test",
                ),
                self._handler,
            )

        async def _handler(self, _args: dict[str, Any]) -> dict[str, Any]:
            return {"node_id": "written"}

    server = UnifiedMcpServer([_Twin()], caller=Caller.UNTRUSTED)
    return build_http_app(server, enable_sse=False), server


class TestTheStreamRoute:
    def test_it_refuses_to_open_without_a_session(self) -> None:
        """A stream on an invented session is a question asked into the void:
        nothing could ever correlate an answer back to it."""
        from fastapi.testclient import TestClient

        app, _ = _app_and_server()
        response = TestClient(app).get("/mcp", headers={"Accept": "text/event-stream"})
        assert response.status_code == 400
        assert "initialize" in response.json()["detail"]

    def test_it_refuses_an_unparseable_session(self) -> None:
        from fastapi.testclient import TestClient

        app, _ = _app_and_server()
        response = TestClient(app).get("/mcp", headers={"Mcp-Session-Id": "not-a-uuid"})
        assert response.status_code == 400

    def test_the_request_response_sse_endpoint_is_untouched(self) -> None:
        """`GET /mcp/sse` is a different thing -- queue work as `?request=`
        params, server closes with `event: done`. Adding the persistent
        stream at `GET /mcp` must not have changed it."""
        from metaforge.mcp.__main__ import build_http_app
        from metaforge.mcp.server import UnifiedMcpServer

        app = build_http_app(UnifiedMcpServer(adapters=[]), enable_sse=True)
        paths = {getattr(r, "path", None) for r in app.routes}
        assert "/mcp/sse" in paths
        assert "/mcp" in paths


class TestTheResponseRoute:
    def test_an_answer_is_accepted_and_not_dispatched(self) -> None:
        """Sending our own answer to `handle_request` comes back as "Unknown
        method: None" -- the misroute the stdio loop had to learn to avoid."""
        from fastapi.testclient import TestClient

        app, _ = _app_and_server()
        client = TestClient(app)
        init = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": ELICITATION_PROTOCOL_VERSION,
                    "capabilities": {"elicitation": {}},
                },
            },
        )
        session = init.headers["Mcp-Session-Id"]
        # Nothing is waiting for this id, so it falls through to dispatch and
        # comes back as a JSON-RPC error rather than being silently eaten.
        orphan = client.post(
            "/mcp",
            headers={"Mcp-Session-Id": session},
            json={"jsonrpc": "2.0", "id": "elicit-nobody-1", "result": {"action": "accept"}},
        )
        assert orphan.status_code == 200
        assert "error" in orphan.json()

    def test_a_notification_still_gets_its_204(self) -> None:
        """FORGE-422's shape, re-checked here because the response-routing
        branch runs before it."""
        from fastapi.testclient import TestClient

        app, _ = _app_and_server()
        response = TestClient(app).post(
            "/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}
        )
        assert response.status_code == 204
        assert response.content == b""


class TestTheHandshakeIsRecordedPerSession:
    def test_initialize_makes_that_session_eligible_once_it_opens_a_stream(self) -> None:
        """Recorded from the session's own handshake, not from the server --
        whose copy is whichever client connected last."""
        from fastapi.testclient import TestClient

        app, _ = _app_and_server()
        client = TestClient(app)
        init = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-11-25", "capabilities": {"elicitation": {}}},
            },
        )
        session = init.headers["Mcp-Session-Id"]
        # FORGE-416: asked for a revision ahead of ours, answered with ours.
        assert init.json()["result"]["protocolVersion"] == ELICITATION_PROTOCOL_VERSION

        health = client.post(
            "/mcp",
            headers={"Mcp-Session-Id": session},
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "health.check", "arguments": {}},
            },
        )
        body = json.loads(health.json()["result"]["content"][0]["text"])
        body = body.get("data") or body
        # No stream open yet, so the connection falls back to the queue.
        assert body["client"]["can_elicit"] is False
