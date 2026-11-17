"""Approval prompts stay open in the client until the approval is decided.

Seen on staging with OpenCode: a held ``project.create`` was put to the person
in their client, and 20 seconds later the prompt was withdrawn
(``mcp_elicitation_withdrawn reason='approval window ended'``) as the call
returned. From then on only the dashboard could decide it. Now:

* a held call's prompt stays open after the call returns, until the approval
  is decided on any surface or expires (capped at an hour), and an answer to
  it is recorded exactly as an in-call answer is;
* an approval this session has not been asked about is put to the person
  unasked, once per session, never for the design-flow worker, and only where
  the client can be prompted at all;
* the default hold lifetime is an hour.

Held calls are driven against the real gateway routers over ASGI, as the
durable-approval tests are, so the ledger entry is what is asserted on.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from api_gateway.approvals.policy import PolicyStore, reset_policy_store
from mcp_core import elicitation
from mcp_core.approval_hold import DEFAULT_TOOL_APPROVAL_TTL_SECONDS, tool_approval_ttl_seconds
from mcp_core.context import McpCallContext, with_context
from mcp_core.elicitation import ElicitAction, ElicitResult, current_prompt_lifetime
from mcp_core.guardrails import Caller
from metaforge.mcp import server as server_mod
from metaforge.mcp import standing_prompts
from metaforge.mcp.http_elicitation import ElicitationHub
from metaforge.mcp.remote_approvals import RemoteApprovalLedger, build_remote_approval_gate
from metaforge.mcp.server import UnifiedMcpServer
from tool_registry.mcp_server.handlers import ToolManifest
from tool_registry.mcp_server.server import McpToolServer

LIST = "/v1/chat/tool_approvals"
PROJECT = "p-drone"
SESSION = uuid.UUID(int=7)


@pytest.fixture(autouse=True)
def _stores(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from api_gateway.chat.tool_approvals import reset_approval_store

    monkeypatch.delenv("METAFORGE_TOOL_APPROVAL_TTL_S", raising=False)
    monkeypatch.setattr(server_mod, "HANDOFF_WAIT_SECONDS", 0.0)
    monkeypatch.setattr(server_mod, "ELICITATION_WAIT_SECONDS", 0.05)
    monkeypatch.setattr(standing_prompts, "POLL_SECONDS", 0.02)
    # Answers here are a person's, however fast they come back.
    monkeypatch.setattr(elicitation, "UNATTENDED_ANSWER_SECONDS", 0.0)
    monkeypatch.setattr(standing_prompts, "UNATTENDED_ANSWER_SECONDS", 0.0)
    reset_approval_store()
    reset_policy_store(PolicyStore())
    yield
    reset_policy_store(None)
    reset_approval_store()


@pytest.fixture
def gateway() -> httpx.AsyncClient:
    from fastapi import FastAPI

    from api_gateway.approvals.routes import router as approvals_router
    from api_gateway.chat.tool_approvals import router

    app = FastAPI()
    app.include_router(router)
    app.include_router(approvals_router)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway.test")


class _Client:
    """A client that shows every prompt and lets the test answer or watch it."""

    def __init__(self) -> None:
        self.asked: list[tuple[str, dict[str, Any]]] = []
        self.answers: list[asyncio.Future[ElicitResult]] = []
        self.withdrawn: list[str] = []
        self.lifetimes: list[float | None] = []

    async def __call__(self, message: str, schema: dict[str, Any]) -> ElicitResult:
        self.asked.append((message, schema))
        self.lifetimes.append(current_prompt_lifetime())
        answer: asyncio.Future[ElicitResult] = asyncio.get_running_loop().create_future()
        self.answers.append(answer)
        try:
            return await answer
        except asyncio.CancelledError as exc:
            self.withdrawn.append(str(exc.args[0]) if exc.args else "")
            raise


def _adapter(ran: list[str]) -> McpToolServer:
    class _Spy(McpToolServer):
        def __init__(self) -> None:
            super().__init__(adapter_id="spy", version="0.1.0")
            for tool in ("twin.commit_geometry", "twin.get_node"):
                self.register_tool(
                    ToolManifest(
                        tool_id=tool, adapter_id="spy", name=tool, description="", capability="t"
                    ),
                    self._handler(tool),
                )

        def _handler(self, tool: str) -> Any:
            async def handle(args: dict[str, Any]) -> dict[str, Any]:
                ran.append(tool)
                return {"ok": True}

            return handle

    return _Spy()


async def _server(
    ran: list[str], client: Any, http: httpx.AsyncClient | None = None
) -> UnifiedMcpServer:
    server = UnifiedMcpServer(
        [_adapter(ran)],
        caller=Caller.REMOTE,
        approval_gate=(
            build_remote_approval_gate("http://gateway.test", client=http) if http else None
        ),
        approval_ledger=RemoteApprovalLedger("http://gateway.test", client=http) if http else None,
        elicitor=client,
    )
    await server.handle_request(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": "1",
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"elicitation": {}} if client is not None else {},
                    "clientInfo": {"name": "opencode", "version": "2.3.0"},
                },
            }
        )
    )
    return server


async def _call(
    server: UnifiedMcpServer, tool: str = "twin.commit_geometry", **ctx: Any
) -> dict[str, Any]:
    context = McpCallContext(session_id=ctx.pop("session", SESSION), **ctx)
    with with_context(context):
        return json.loads(
            await server.handle_request(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 7,
                        "method": "tools/call",
                        "params": {
                            "name": tool,
                            "arguments": {"project_id": PROJECT, "obj_id": "bracket"},
                        },
                    }
                )
            )
        )


async def _entry(http: httpx.AsyncClient) -> dict[str, Any]:
    [entry] = (await http.get(LIST, params={"status": "all"})).json()["runs"]
    return dict(entry)


async def _until(check: Any, attempts: int = 200) -> None:
    for _ in range(attempts):
        if await check() if asyncio.iscoroutinefunction(check) else check():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition never became true")


# -- the hold lifetime ----------------------------------------------------------


def test_a_hold_lasts_an_hour_by_default_and_at_most_an_hour() -> None:
    assert DEFAULT_TOOL_APPROVAL_TTL_SECONDS == 3600.0
    assert tool_approval_ttl_seconds({}) == 3600.0
    assert tool_approval_ttl_seconds({"METAFORGE_TOOL_APPROVAL_TTL_S": "900"}) == 900.0
    assert tool_approval_ttl_seconds({"METAFORGE_TOOL_APPROVAL_TTL_S": "7200"}) == 3600.0


# -- a held call's prompt outlives the call ---------------------------------------


@pytest.mark.asyncio
class TestTheHeldCallsPromptStaysOpen:
    async def test_it_outlives_the_call_and_is_withdrawn_when_decided_on_the_dashboard(
        self, gateway: httpx.AsyncClient
    ) -> None:
        ran: list[str] = []
        client = _Client()
        async with gateway as http:
            server = await _server(ran, client, http)
            held = (await _call(server))["result"]
            approval_id = held["_meta"]["approval"]["approval_id"]
            assert held["_meta"]["approval"]["expires_in_seconds"] > 3500
            # Well past the in-call window: still on screen, nothing withdrawn.
            await asyncio.sleep(0.3)
            assert len(client.asked) == 1 and not client.answers[0].done()
            assert client.withdrawn == []
            # The prompt may wait as long as the approval lives.
            assert client.lifetimes[0] is not None and client.lifetimes[0] > 3500
            assert server._standing.is_open(str(SESSION), approval_id)  # noqa: SLF001

            decided = await http.post(f"{LIST}/{approval_id}", json={"decision": "approve"})
            assert decided.status_code == 200
            await _until(lambda: client.withdrawn)

        assert client.withdrawn == ["approval decided elsewhere"]
        assert not server._standing.is_open(str(SESSION), approval_id)  # noqa: SLF001
        assert ran == []

    async def test_answering_it_late_records_the_decision_and_the_retry_runs_once(
        self, gateway: httpx.AsyncClient
    ) -> None:
        ran: list[str] = []
        client = _Client()
        async with gateway as http:
            server = await _server(ran, client, http)
            held = (await _call(server))["result"]
            assert held["isError"] is True
            await asyncio.sleep(0.2)
            client.answers[0].set_result(ElicitResult(ElicitAction.ACCEPT, {}))

            async def decided() -> bool:
                return (await _entry(http))["status"] != "awaiting_approval"

            await _until(decided)
            entry = await _entry(http)
            first = await _call(server)
            second = await _call(server)

        assert entry["approved_by"] == "local:elicitation"
        assert first["result"]["isError"] is False
        assert ran == ["twin.commit_geometry"]
        # Single use: the next identical call is a new hold, and is asked anew.
        assert second["result"]["isError"] is True
        assert second["result"]["_meta"]["approval"]["approval_id"] != entry["id"]

    async def test_a_decline_in_the_late_prompt_is_a_refusal(
        self, gateway: httpx.AsyncClient
    ) -> None:
        ran: list[str] = []
        client = _Client()
        async with gateway as http:
            server = await _server(ran, client, http)
            await _call(server)
            await asyncio.sleep(0.1)
            client.answers[0].set_result(ElicitResult(ElicitAction.DECLINE, {}))

            async def decided() -> bool:
                return (await _entry(http))["status"] != "awaiting_approval"

            await _until(decided)
            retry = await _call(server)

        # The refusal is reported once, and nothing ran.
        assert ran == []
        assert "error" in retry or retry["result"]["isError"] is True

    async def test_dismissing_it_decides_nothing(self, gateway: httpx.AsyncClient) -> None:
        ran: list[str] = []
        client = _Client()
        async with gateway as http:
            server = await _server(ran, client, http)
            await _call(server)
            await asyncio.sleep(0.1)
            client.answers[0].set_result(ElicitResult(ElicitAction.CANCEL, {}))
            await asyncio.sleep(0.2)
            entry = await _entry(http)

        assert entry["status"] == "awaiting_approval"
        assert ran == []

    async def test_expiry_withdraws_it(
        self, gateway: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(server_mod, "MAX_PROMPT_SECONDS", 0.3)
        ran: list[str] = []
        client = _Client()
        async with gateway as http:
            server = await _server(ran, client, http)
            await _call(server)
            await _until(lambda: client.withdrawn)

        assert client.withdrawn == ["approval expired"]
        assert ran == []

    async def test_the_same_hold_is_not_asked_again_on_a_repeat_call(
        self, gateway: httpx.AsyncClient
    ) -> None:
        ran: list[str] = []
        client = _Client()
        async with gateway as http:
            server = await _server(ran, client, http)
            await _call(server)
            again = await _call(server)

        assert again["result"]["_meta"]["approval"]["repeated"] is True
        assert len(client.asked) == 1


# -- approvals the session has not been asked about --------------------------------


class _Lister:
    def __init__(self, items: list[dict[str, Any]]) -> None:
        self.items = items

    async def __call__(self, project_id: str | None) -> list[dict[str, Any]]:
        return [dict(i) for i in self.items]


class _Decider:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    async def __call__(self, *args: Any) -> dict[str, Any]:
        self.calls.append(args)
        return {"decision": {"approver": args[4]}}


GATE = {
    "id": "gate:run_1",
    "status": "pending",
    "kind": "gate",
    "title": "Design gate",
    "project_id": PROJECT,
    "decidable": True,
    "allowed_decisions": ["approve", "reject"],
    "reason_required_for": ["reject"],
}
HELD = {
    "id": "tool:run_2",
    "status": "pending",
    "kind": "tool_call",
    "title": "Tool call: project.create",
    "project_id": None,
    "decidable": True,
    "allowed_decisions": ["approve", "reject"],
    "reason_required_for": ["reject"],
}


async def _prompting_server(
    client: Any, items: list[dict[str, Any]]
) -> tuple[UnifiedMcpServer, _Lister, _Decider]:
    lister, decider = _Lister(items), _Decider()
    server = await _server([], client)
    server.attach_approval_notices(lister, decider)
    return server, lister, decider


async def _notice(server: UnifiedMcpServer, **ctx: Any) -> Any:
    context = McpCallContext(session_id=ctx.pop("session", SESSION), **ctx)
    with with_context(context):
        return await server._approval_notice("twin.get_node")  # noqa: SLF001


@pytest.mark.asyncio
class TestUnaskedApprovalsArePrompted:
    async def test_a_new_approval_is_put_to_the_person_once(self) -> None:
        client = _Client()
        server, lister, decider = await _prompting_server(client, [GATE])

        first = await _call(server, "twin.get_node")
        await asyncio.sleep(0.05)
        server._pending_cache.clear()  # noqa: SLF001
        await _call(server, "twin.get_node")

        # The notice still goes out, for the agent and clients without prompts.
        assert "Design gate" in " ".join(c["text"] for c in first["result"]["content"])
        assert len(client.asked) == 1
        message, schema = client.asked[0]
        assert "Design gate" in message and "stays open until it is decided" in message
        assert schema["properties"]["decision"]["enum"] == ["approve", "reject"]

        # A new item is asked about; the old one is not asked again.
        lister.items = [GATE, HELD]
        server._pending_cache.clear()  # noqa: SLF001
        await _call(server, "twin.get_node")
        assert len(client.asked) == 2 and "project.create" in client.asked[1][0]

        client.answers[0].set_result(
            ElicitResult(ElicitAction.ACCEPT, {"decision": "approve", "reason": ""})
        )
        await _until(lambda: decider.calls)
        assert decider.calls[0][:2] == ("gate:run_1", "approve")
        assert decider.calls[0][4] == "local:elicitation"

    async def test_a_reason_is_still_required_where_the_item_says_so(self) -> None:
        client = _Client()
        server, _, decider = await _prompting_server(client, [GATE])
        await _notice(server)
        client.answers[0].set_result(ElicitResult(ElicitAction.ACCEPT, {"decision": "reject"}))
        await asyncio.sleep(0.1)
        assert decider.calls == []

    async def test_it_is_withdrawn_when_decided_elsewhere(self) -> None:
        client = _Client()
        server, lister, decider = await _prompting_server(client, [GATE])
        await _notice(server)
        lister.items = []
        server._pending_cache.clear()  # noqa: SLF001
        await _until(lambda: client.withdrawn)
        assert client.withdrawn == ["approval decided elsewhere"]
        assert decider.calls == []

    async def test_the_design_flow_worker_is_never_prompted(self) -> None:
        client = _Client()
        server, _, _ = await _prompting_server(client, [GATE])
        assert await _notice(server, service_verified=True) is None
        assert client.asked == []

    async def test_a_client_that_cannot_be_prompted_gets_the_notice_only(self) -> None:
        server, _, _ = await _prompting_server(None, [GATE])
        sentence, count = await _notice(server)
        assert count == 1 and "Design gate" in sentence

    async def test_only_items_this_caller_may_see_are_prompted(self) -> None:
        client = _Client()
        other = {**GATE, "id": "gate:run_9", "project_id": "p-other"}
        server, _, _ = await _prompting_server(client, [other, GATE])
        await _notice(server, allowed_projects=frozenset({PROJECT}))
        assert [m for m, _ in client.asked if "Design gate" in m]
        assert len(client.asked) == 1

    async def test_an_item_that_cannot_be_decided_now_is_not_prompted(self) -> None:
        client = _Client()
        stuck = {**GATE, "decidable": False, "not_decidable_reason": "a run holds it"}
        server, _, _ = await _prompting_server(client, [stuck])
        assert await _notice(server) is not None
        assert client.asked == []

    async def test_at_most_a_few_are_open_at_once(self) -> None:
        client = _Client()
        items = [{**GATE, "id": f"gate:run_{n}", "title": f"Gate {n}"} for n in range(6)]
        server, _, _ = await _prompting_server(client, items)
        await _notice(server)
        assert len(client.asked) == server_mod.AUTO_PROMPTS_OPEN_PER_SESSION
        # One answered (dismissed) frees room; the next reply asks the next.
        client.answers[0].set_result(ElicitResult(ElicitAction.CANCEL, {}))
        await asyncio.sleep(0.05)
        server._pending_cache.clear()  # noqa: SLF001
        await _notice(server)
        assert len(client.asked) == server_mod.AUTO_PROMPTS_OPEN_PER_SESSION + 1
        assert len({m for m, _ in client.asked}) == len(client.asked)

    async def test_each_session_is_asked_separately(self) -> None:
        client = _Client()
        server, _, _ = await _prompting_server(client, [GATE])
        await _notice(server, session=uuid.UUID(int=1))
        await _notice(server, session=uuid.UUID(int=2))
        assert len(client.asked) == 2


# -- the HTTP hub ------------------------------------------------------------------


@pytest.mark.asyncio
class TestTheHub:
    async def test_a_prompt_waits_as_long_as_its_lifetime_not_the_hub_timeout(self) -> None:
        hub = ElicitationHub(timeout_seconds=0.05)
        hub.note_initialize("s", capabilities={"elicitation": {}}, negotiated_protocol="2025-06-18")
        stream = hub.stream("s")
        await stream.__anext__()  # opened
        with elicitation.prompt_lifetime(5.0):
            question = asyncio.ensure_future(hub.elicit("s", "q", {}))
        frame = await stream.__anext__()
        message_id = json.loads(frame.decode().split("data: ", 1)[1])["id"]
        await asyncio.sleep(0.2)  # past the hub's own timeout
        assert not question.done()
        assert hub.resolve(
            {"jsonrpc": "2.0", "id": message_id, "result": {"action": "accept", "content": {}}}
        )
        assert (await question).action is ElicitAction.ACCEPT
        await stream.aclose()

    async def test_closing_the_session_stream_drops_its_prompts_undecided(self) -> None:
        hub = ElicitationHub()
        hub.note_initialize("s", capabilities={"elicitation": {}}, negotiated_protocol="2025-06-18")
        stream = hub.stream("s")
        await stream.__anext__()
        with elicitation.prompt_lifetime(60.0):
            question = asyncio.ensure_future(hub.elicit("s", "q", {}))
        await stream.__anext__()
        await stream.aclose()  # the client went away
        assert (await asyncio.wait_for(question, 1.0)).action is ElicitAction.CANCEL

    async def test_a_withdrawal_carries_its_reason(self) -> None:
        hub = ElicitationHub()
        hub.note_initialize("s", capabilities={"elicitation": {}}, negotiated_protocol="2025-06-18")
        stream = hub.stream("s")
        await stream.__anext__()
        question = asyncio.ensure_future(hub.elicit("s", "q", {}))
        await stream.__anext__()
        question.cancel("approval decided elsewhere")
        with pytest.raises(asyncio.CancelledError):
            await question
        notice = json.loads((await stream.__anext__()).decode().split("data: ", 1)[1])
        assert notice["method"] == "notifications/cancelled"
        assert notice["params"]["reason"] == "approval decided elsewhere"
        await stream.aclose()


# -- over HTTP, a client with no GET /mcp (OpenCode on staging) ------------------


@pytest.mark.asyncio
class TestTheCallStreamCarriesTheOpenPrompt:
    """The held call's own stream stays open for its prompt after the result.

    Driven over raw ASGI with the call-stream tests' exchange helper, because
    a buffered test client would never return a stream that is still open.
    """

    async def _setup(self, http: httpx.AsyncClient) -> tuple[Any, Any, str]:
        from metaforge.mcp.__main__ import build_http_app
        from tests.unit.test_http_elicitation_call_stream import (
            _Client as AsgiClient,
        )
        from tests.unit.test_http_elicitation_call_stream import (
            _initialize,
            _Projects,
        )

        projects = _Projects()
        server = UnifiedMcpServer(
            [projects],
            caller=Caller.UNTRUSTED,
            approval_gate=build_remote_approval_gate("http://gateway.test", client=http),
            approval_ledger=RemoteApprovalLedger("http://gateway.test", client=http),
        )
        client = AsgiClient(build_http_app(server, enable_sse=False))
        session = await _initialize(client)
        return client, projects, session

    @staticmethod
    def _create() -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "project.create", "arguments": {"name": "drone"}},
        }

    async def test_the_result_comes_back_and_the_prompt_stays_until_answered(
        self, gateway: httpx.AsyncClient
    ) -> None:
        async with gateway as http:
            client, projects, session = await self._setup(http)
            call = client.post(self._create(), session=session)
            await call.start()
            ask = await call.next_event()
            assert ask is not None and ask["method"] == "elicitation/create"
            # The call returns promptly, held, on the same stream.
            result = await call.next_event()
            assert result is not None and result["id"] == 1
            assert result["result"]["_meta"]["approval"]["held"] is True
            assert projects.calls == []
            # The stream is still open, and nothing withdrew the question.
            pending = asyncio.ensure_future(call.next_event())
            await asyncio.sleep(0.3)
            assert not pending.done()
            # The person answers well after the call returned.
            reply, _ = await client.post_json(
                {"jsonrpc": "2.0", "id": ask["id"], "result": {"action": "accept"}},
                session=session,
            )
            assert reply.status == 202
            assert await asyncio.wait_for(pending, 5) is None  # the stream closes

            async def decided() -> bool:
                return (await _entry(http))["status"] != "awaiting_approval"

            await _until(decided)
            entry = await _entry(http)
            # The identical retry runs, once.
            again, body = await client.post_json(self._create(), session=session)

        assert entry["approved_by"] == "local:elicitation"
        assert body["result"]["isError"] is False
        assert projects.calls == [{"name": "drone"}]

    async def test_a_client_that_hangs_up_drops_the_prompt_undecided(
        self, gateway: httpx.AsyncClient
    ) -> None:
        async with gateway as http:
            client, projects, session = await self._setup(http)
            call = client.post(self._create(), session=session)
            await call.start()
            assert (await call.next_event()) is not None  # the question
            assert (await call.next_event()) is not None  # the held result
            call.hang_up()
            await asyncio.wait_for(call.task, timeout=5)
            await asyncio.sleep(0.1)
            entry = await _entry(http)

        assert entry["status"] == "awaiting_approval"
        assert projects.calls == []
