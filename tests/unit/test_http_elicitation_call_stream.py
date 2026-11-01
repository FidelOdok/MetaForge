"""Inline approvals on the call's own stream (FORGE-464).

FORGE-423 pushed `elicitation/create` only on the standalone `GET /mcp`
stream. Claude Code 2.1.286 never opens that stream, so `can_elicit` stayed
false for it and every held write went to the dashboard queue. The Streamable
HTTP spec lets a server answer a POST with `text/event-stream` and send
requests related to that call before the result, which is what an approval
is. These tests drive the real sidecar app over raw ASGI, because Starlette's
`TestClient` buffers a whole response before returning it: a stream that is
waiting on our own answer would never come back.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from mcp_core.elicitation import ELICITATION_PROTOCOL_VERSION
from mcp_core.guardrails import Caller
from metaforge.mcp.__main__ import _accepts_event_stream, build_http_app
from metaforge.mcp.server import UnifiedMcpServer
from tool_registry.mcp_server.handlers import ToolManifest
from tool_registry.mcp_server.server import McpToolServer

_BOTH = "application/json, text/event-stream"


class _Projects(McpToolServer):
    """`project.create` is held for an untrusted caller; `health.ping` is not."""

    def __init__(self) -> None:
        super().__init__(adapter_id="project", version="0.1.0")
        self.calls: list[dict[str, Any]] = []
        self.register_tool(
            ToolManifest(
                tool_id="project.create",
                adapter_id="project",
                name="create project",
                description="stub",
                capability="test",
            ),
            self._create,
        )

    async def _create(self, args: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(args)
        return {"project_id": "p-1", "name": args.get("name")}


class _Exchange:
    """One in-flight ASGI request: its status, headers and body chunks."""

    def __init__(self, app: Any, method: str, body: bytes, headers: dict[str, str]) -> None:
        self._sent: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._disconnect = asyncio.Event()
        self._body_given = False
        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": "/mcp",
            "raw_path": b"/mcp",
            "query_string": b"",
            "root_path": "",
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
            "client": ("127.0.0.1", 1234),
            "server": ("testserver", 80),
        }
        self.task = asyncio.create_task(app(scope, self._receive, self._sent.put))
        self._body = body
        self._buffer = ""
        self.status = 0
        self.headers: dict[str, str] = {}

    async def _receive(self) -> dict[str, Any]:
        if not self._body_given:
            self._body_given = True
            return {"type": "http.request", "body": self._body, "more_body": False}
        await self._disconnect.wait()
        return {"type": "http.disconnect"}

    def hang_up(self) -> None:
        self._disconnect.set()

    async def start(self) -> None:
        message = await asyncio.wait_for(self._sent.get(), timeout=5)
        assert message["type"] == "http.response.start"
        self.status = message["status"]
        self.headers = {k.decode().lower(): v.decode() for k, v in message["headers"]}

    async def _chunk(self) -> tuple[bytes, bool]:
        message = await asyncio.wait_for(self._sent.get(), timeout=5)
        assert message["type"] == "http.response.body"
        return message.get("body", b""), bool(message.get("more_body", False))

    async def read_all(self) -> bytes:
        out = b""
        while True:
            chunk, more = await self._chunk()
            out += chunk
            if not more:
                return out

    async def next_event(self) -> dict[str, Any] | None:
        """The next SSE `data:` payload, skipping comments. None at end."""
        while True:
            if "\n\n" in self._buffer:
                block, self._buffer = self._buffer.split("\n\n", 1)
                data = [ln[6:] for ln in block.splitlines() if ln.startswith("data: ")]
                if data:
                    return json.loads("\n".join(data))
                continue
            chunk, more = await self._chunk()
            self._buffer += chunk.decode()
            if not more and "\n\n" not in self._buffer:
                return None


class _Client:
    def __init__(self, app: Any) -> None:
        self.app = app

    def post(
        self, payload: dict[str, Any], *, session: str | None = None, accept: str = _BOTH
    ) -> _Exchange:
        headers = {"content-type": "application/json", "accept": accept}
        if session:
            headers["mcp-session-id"] = session
        return _Exchange(self.app, "POST", json.dumps(payload).encode(), headers)

    async def post_json(self, payload: dict[str, Any], **kw: Any) -> tuple[_Exchange, Any]:
        exchange = self.post(payload, **kw)
        await exchange.start()
        body = await exchange.read_all()
        return exchange, (json.loads(body) if body else None)


def _app() -> tuple[_Client, _Projects]:
    projects = _Projects()
    server = UnifiedMcpServer([projects], caller=Caller.UNTRUSTED)
    return _Client(build_http_app(server, enable_sse=False)), projects


async def _initialize(client: _Client, *, elicitation: bool = True) -> str:
    caps: dict[str, Any] = {"elicitation": {}} if elicitation else {}
    exchange, _ = await client.post_json(
        {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {
                "protocolVersion": ELICITATION_PROTOCOL_VERSION,
                "capabilities": caps,
                "clientInfo": {"name": "claude-code"},
            },
        }
    )
    return exchange.headers["mcp-session-id"]


def _create(call_id: int = 1) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": call_id,
        "method": "tools/call",
        "params": {"name": "project.create", "arguments": {"name": "drone"}},
    }


def _answer(request: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request["id"], "result": result}


def _tool_text(response: dict[str, Any]) -> str:
    return json.dumps(response)


@pytest.mark.asyncio
class TestTheCallCarriesItsOwnQuestion:
    async def test_accept_runs_the_tool_and_the_result_arrives_on_the_same_stream(
        self,
    ) -> None:
        """The acceptance path, and no GET /mcp anywhere in it."""
        client, projects = _app()
        session = await _initialize(client)

        call = client.post(_create(), session=session)
        await call.start()
        assert call.status == 200
        assert call.headers["content-type"].startswith("text/event-stream")
        assert call.headers["mcp-session-id"] == session

        ask = await call.next_event()
        assert ask is not None and ask["method"] == "elicitation/create"
        assert "project.create" in ask["params"]["message"]
        assert projects.calls == [], "the tool ran before anyone approved it"

        reply, body = await client.post_json(
            _answer(ask, {"action": "accept", "content": {"approve": True}}), session=session
        )
        assert reply.status == 202 and body is None

        result = await call.next_event()
        assert result is not None and result["id"] == 1
        assert "result" in result
        assert "p-1" in _tool_text(result)
        assert projects.calls == [{"name": "drone"}]
        assert await call.next_event() is None, "the stream stayed open after the result"

    @pytest.mark.parametrize(
        ("answer", "named"),
        [
            ({"action": "decline"}, "a reviewer rejected it"),
            ({"action": "accept", "content": {"approve": False}}, "a reviewer rejected it"),
            ({"action": "cancel"}, "no one answered"),
        ],
    )
    async def test_a_refusal_is_named_and_the_tool_does_not_run(
        self, answer: dict[str, Any], named: str
    ) -> None:
        client, projects = _app()
        session = await _initialize(client)
        call = client.post(_create(), session=session)
        await call.start()
        ask = await call.next_event()
        assert ask is not None
        await client.post_json(_answer(ask, answer), session=session)

        result = await call.next_event()
        assert result is not None and result["id"] == 1
        assert named in _tool_text(result)
        assert projects.calls == []

    async def test_a_client_that_hangs_up_mid_question_is_not_left_waiting(self) -> None:
        """The question is cancelled at once rather than at the elicitation
        timeout, so the call ends as TIMED_OUT and nothing is written."""
        client, projects = _app()
        session = await _initialize(client)
        call = client.post(_create(), session=session)
        await call.start()
        assert (await call.next_event()) is not None
        call.hang_up()
        await asyncio.wait_for(call.task, timeout=5)
        await asyncio.sleep(0.05)
        assert projects.calls == []


@pytest.mark.asyncio
class TestWhoGetsAStream:
    async def test_a_call_that_is_not_held_keeps_its_plain_json(self) -> None:
        """Most calls never ask anything. Their response must not change."""
        client, _ = _app()
        session = await _initialize(client)
        exchange, body = await client.post_json(
            {"jsonrpc": "2.0", "id": 5, "method": "tools/list", "params": {}}, session=session
        )
        assert exchange.headers["content-type"].startswith("application/json")
        assert body["id"] == 5

    async def test_health_check_on_a_streamable_call_reports_can_elicit(self) -> None:
        """What the docs promise: the snapshot is taken inside the call, and a
        declared session sending `text/event-stream` can be asked."""
        client, _ = _app()
        session = await _initialize(client)
        payload = {
            "jsonrpc": "2.0",
            "id": 9,
            "method": "tools/call",
            "params": {"name": "health.check", "arguments": {}},
        }
        for accept, expected in ((_BOTH, True), ("*/*", False)):
            exchange, body = await client.post_json(payload, session=session, accept=accept)
            assert exchange.headers["content-type"].startswith("application/json")
            report = json.loads(body["result"]["content"][0]["text"])
            report = report.get("data") or report
            assert report["client"]["can_elicit"] is expected, accept

    async def test_without_event_stream_in_accept_it_falls_back_to_the_queue(self) -> None:
        """No approval gate is configured here, so the fallback is the named
        "not configured" refusal rather than a question nobody can read."""
        client, projects = _app()
        session = await _initialize(client)
        exchange, body = await client.post_json(_create(), session=session, accept="*/*")
        assert exchange.headers["content-type"].startswith("application/json")
        assert "elicitation/create" not in json.dumps(body)
        assert projects.calls == []

    async def test_a_session_that_did_not_declare_elicitation_is_not_asked(self) -> None:
        client, projects = _app()
        session = await _initialize(client, elicitation=False)
        exchange, body = await client.post_json(_create(), session=session)
        assert exchange.headers["content-type"].startswith("application/json")
        assert "elicitation/create" not in json.dumps(body)
        assert projects.calls == []

    async def test_no_session_no_stream(self) -> None:
        client, projects = _app()
        exchange, body = await client.post_json(_create())
        assert exchange.headers["content-type"].startswith("application/json")
        assert projects.calls == []


@pytest.mark.asyncio
class TestAnOpenSessionStreamStillWins:
    async def test_a_session_with_get_mcp_open_is_not_switched_to_a_call_stream(self) -> None:
        """FORGE-423's clients are already listening on GET /mcp. Moving
        their question onto the POST would change what they read mid-call."""
        from metaforge.mcp.http_elicitation import ElicitationHub

        hub = ElicitationHub()
        session = "33333333-3333-3333-3333-333333333333"
        hub.note_initialize(
            session,
            capabilities={"elicitation": {}},
            negotiated_protocol=ELICITATION_PROTOCOL_VERSION,
        )
        assert hub.call_stream_allowed(session) is True
        stream = hub.stream(session)
        await stream.__anext__()  # the open banner; the stream is now live
        assert hub.call_stream_allowed(session) is False
        await stream.aclose()
        # Closing it keeps what the session declared, so the call stream is
        # available again rather than the session looking undeclared.
        assert hub.call_stream_allowed(session) is True


class TestAcceptParsing:
    @pytest.mark.parametrize(
        ("accept", "expected"),
        [
            ("application/json, text/event-stream", True),
            ("text/event-stream", True),
            ("application/json;q=0.9, TEXT/EVENT-STREAM;q=0.5", True),
            ("*/*", False),
            ("application/json", False),
            ("", False),
            (None, False),
        ],
    )
    def test_it(self, accept: str | None, expected: bool) -> None:
        assert _accepts_event_stream(accept) is expected
