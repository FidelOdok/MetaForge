"""Questions returned inside results for 2026-07-28 clients (FORGE-601).

A held write on a handshake-free request returns ``input_required`` with the
approval question and a signed ``requestState``; the retry carries the answer.
The ledger is the real gateway app over ASGI, as in the FORGE-473 tests.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from mcp_core.guardrails import Caller
from mcp_core.mrtr import (
    InvalidRequestStateError,
    MrtrContext,
    StateSigner,
    call_digest,
    mrtr_elicitor,
)
from mcp_core.protocol_versions import (
    META_CLIENT_CAPABILITIES,
    META_PROTOCOL_VERSION,
    MODERN_PREVIEW_ENV,
)
from metaforge.mcp.remote_approvals import RemoteApprovalLedger
from metaforge.mcp.server import UnifiedMcpServer
from tool_registry.mcp_server.handlers import ToolManifest
from tool_registry.mcp_server.server import McpToolServer

LIST = "/v1/chat/tool_approvals"
ARGS = {"obj_id": "bracket"}


@pytest.fixture(autouse=True)
def _preview(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MODERN_PREVIEW_ENV, "on")


@pytest.fixture
def gateway() -> httpx.AsyncClient:
    from fastapi import FastAPI

    from api_gateway.chat.tool_approvals import reset_approval_store, router

    reset_approval_store()
    app = FastAPI()
    app.include_router(router)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway.test")


def _adapter(ran: list[str]) -> McpToolServer:
    class _Spy(McpToolServer):
        def __init__(self) -> None:
            super().__init__(adapter_id="twin", version="0.1.0")
            self.register_tool(
                ToolManifest(
                    tool_id="twin.commit_geometry",
                    adapter_id="twin",
                    name="commit",
                    description="stub",
                    capability="test",
                ),
                self._handler,
            )

        async def _handler(self, args: dict[str, Any]) -> dict[str, Any]:
            ran.append("twin.commit_geometry")
            return {"ok": True}

    return _Spy()


def _server(http: httpx.AsyncClient, ran: list[str]) -> UnifiedMcpServer:
    return UnifiedMcpServer(
        [_adapter(ran)],
        caller=Caller.REMOTE,
        approval_ledger=RemoteApprovalLedger("http://gateway.test", client=http),
    )


def _params(caps: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
    return {
        "name": "twin.commit_geometry",
        "arguments": ARGS,
        "_meta": {
            META_PROTOCOL_VERSION: "2026-07-28",
            META_CLIENT_CAPABILITIES: {"elicitation": {"form": {}}} if caps is None else caps,
        },
        **extra,
    }


async def _call(server: UnifiedMcpServer, params: dict[str, Any], rid: int = 1) -> dict[str, Any]:
    raw = await server.handle_request(
        json.dumps({"jsonrpc": "2.0", "id": rid, "method": "tools/call", "params": params})
    )
    return dict(json.loads(raw))


async def _entries(http: httpx.AsyncClient) -> list[dict[str, Any]]:
    return list((await http.get(LIST)).json()["runs"])


def _approve(approve: bool = True) -> dict[str, Any]:
    return {"q1": {"action": "accept", "content": {"approve": approve}}}


@pytest.mark.asyncio
class TestAHeldWriteAsksInsideTheResult:
    async def test_the_first_call_returns_the_question_and_runs_nothing(
        self, gateway: httpx.AsyncClient
    ) -> None:
        ran: list[str] = []
        async with gateway as http:
            result = (await _call(_server(http, ran), _params()))["result"]
            entries = await _entries(http)
        assert result["resultType"] == "input_required"
        question = result["inputRequests"]["q1"]
        assert question["method"] == "elicitation/create"
        assert question["params"]["mode"] == "form"
        assert "approve" in question["params"]["requestedSchema"]["properties"]
        assert result["requestState"]
        assert "ttlMs" not in result  # an interim result is not cacheable
        assert ran == []
        assert len(entries) == 1  # the hold is on record before the question

    async def test_the_retry_with_approval_runs_it_once_on_the_same_entry(
        self, gateway: httpx.AsyncClient
    ) -> None:
        ran: list[str] = []
        async with gateway as http:
            server = _server(http, ran)
            first = (await _call(server, _params()))["result"]
            [entry] = await _entries(http)
            retry = await _call(
                server,
                _params(inputResponses=_approve(), requestState=first["requestState"]),
                rid=2,
            )
            after = (await http.get(f"{LIST}/{entry['id']}")).json()
            pending = await _entries(http)
        assert retry["result"]["resultType"] == "complete"
        assert ran == ["twin.commit_geometry"]
        # The same entry, now decided, with the person's answer on record.
        assert after["status"] in {"running", "completed"}
        assert pending == []

    async def test_a_no_does_not_run_it(self, gateway: httpx.AsyncClient) -> None:
        ran: list[str] = []
        async with gateway as http:
            server = _server(http, ran)
            first = (await _call(server, _params()))["result"]
            await _call(
                server,
                _params(inputResponses=_approve(False), requestState=first["requestState"]),
                rid=2,
            )
        assert ran == []

    async def test_replaying_an_approved_answer_is_refused(
        self, gateway: httpx.AsyncClient
    ) -> None:
        ran: list[str] = []
        async with gateway as http:
            server = _server(http, ran)
            first = (await _call(server, _params()))["result"]
            retry = _params(inputResponses=_approve(), requestState=first["requestState"])
            await _call(server, retry, rid=2)
            replay = await _call(server, retry, rid=3)
        assert replay["error"]["code"] == -32602
        assert "already been used" in replay["error"]["message"]
        assert ran == ["twin.commit_geometry"]

    async def test_a_retry_without_the_answer_asks_again_on_the_same_entry(
        self, gateway: httpx.AsyncClient
    ) -> None:
        ran: list[str] = []
        async with gateway as http:
            server = _server(http, ran)
            first = (await _call(server, _params()))["result"]
            again = (await _call(server, _params(requestState=first["requestState"]), rid=2))[
                "result"
            ]
            entries = await _entries(http)
        assert again["resultType"] == "input_required"
        assert len(entries) == 1
        assert ran == []

    async def test_a_dashboard_decision_made_meanwhile_wins(
        self, gateway: httpx.AsyncClient
    ) -> None:
        ran: list[str] = []
        async with gateway as http:
            server = _server(http, ran)
            first = (await _call(server, _params()))["result"]
            [entry] = await _entries(http)
            await http.post(f"{LIST}/{entry['id']}/resolve", json={"outcome": "rejected"})
            await _call(
                server,
                _params(inputResponses=_approve(True), requestState=first["requestState"]),
                rid=2,
            )
        assert ran == []  # the dashboard said no first


@pytest.mark.asyncio
class TestTheStateIsChecked:
    async def test_a_tampered_state_is_refused(self, gateway: httpx.AsyncClient) -> None:
        ran: list[str] = []
        async with gateway as http:
            server = _server(http, ran)
            first = (await _call(server, _params()))["result"]
            body, mac = first["requestState"].rsplit(".", 1)
            forged = body + "." + ("0" if mac[0] != "0" else "1") + mac[1:]
            response = await _call(
                server, _params(inputResponses=_approve(), requestState=forged), rid=2
            )
        assert response["error"]["code"] == -32602
        assert ran == []

    async def test_a_state_for_different_arguments_is_refused(
        self, gateway: httpx.AsyncClient
    ) -> None:
        ran: list[str] = []
        async with gateway as http:
            server = _server(http, ran)
            first = (await _call(server, _params()))["result"]
            other = _params(inputResponses=_approve(), requestState=first["requestState"])
            other["arguments"] = {"obj_id": "something-else"}
            response = await _call(server, other, rid=2)
        assert "different request" in response["error"]["message"]
        assert ran == []

    async def test_answers_without_a_state_are_refused(self, gateway: httpx.AsyncClient) -> None:
        ran: list[str] = []
        async with gateway as http:
            response = await _call(_server(http, ran), _params(inputResponses=_approve()))
        assert response["error"]["code"] == -32602
        assert ran == []


@pytest.mark.asyncio
class TestAClientThatCannotBeAsked:
    async def test_no_form_elicitation_means_no_question(self, gateway: httpx.AsyncClient) -> None:
        ran: list[str] = []
        async with gateway as http:
            response = await _call(_server(http, ran), _params(caps={}))
        assert response.get("result", {}).get("resultType") != "input_required"
        assert ran == []


class TestTheSigner:
    def _ctx(self, **over: Any) -> MrtrContext:
        base = {"principal": "alice", "method": "tools/call", "digest": "d1"}
        base.update(over)
        return MrtrContext(**base)

    def test_round_trip_carries_the_holds(self) -> None:
        signer = StateSigner(b"k")
        ctx = self._ctx(holds={"q1": "run_1"})
        assert signer.verify(
            signer.sign(ctx), principal="alice", method="tools/call", digest="d1"
        ) == {"q1": "run_1"}

    @pytest.mark.parametrize(
        ("principal", "method", "digest", "fragment"),
        [
            ("bob", "tools/call", "d1", "different caller"),
            ("alice", "prompts/get", "d1", "different request"),
            ("alice", "tools/call", "d2", "different request"),
        ],
    )
    def test_it_is_bound(self, principal: str, method: str, digest: str, fragment: str) -> None:
        signer = StateSigner(b"k")
        token = signer.sign(self._ctx())
        with pytest.raises(InvalidRequestStateError, match=fragment):
            signer.verify(token, principal=principal, method=method, digest=digest)

    def test_it_expires(self) -> None:
        now = [1000.0]
        signer = StateSigner(b"k", clock=lambda: now[0])
        token = signer.sign(self._ctx())
        now[0] += 10_000
        with pytest.raises(InvalidRequestStateError, match="expired"):
            signer.verify(token, principal="alice", method="tools/call", digest="d1")

    def test_another_key_does_not_verify(self) -> None:
        token = StateSigner(b"one").sign(self._ctx())
        with pytest.raises(InvalidRequestStateError, match="verification"):
            StateSigner(b"two").verify(token, principal="alice", method="tools/call", digest="d1")

    def test_an_approval_is_single_use(self) -> None:
        signer = StateSigner(b"k")
        signer.consume("run_1")
        with pytest.raises(InvalidRequestStateError, match="already been used"):
            signer.consume("run_1")

    def test_the_digest_ignores_the_retry_fields(self) -> None:
        call = {"name": "t", "arguments": {"a": 1}}
        retry = {**call, "inputResponses": {"q1": {}}, "requestState": "s", "_meta": {"x": 1}}
        assert call_digest("tools/call", call) == call_digest("tools/call", retry)


@pytest.mark.asyncio
class TestTheElicitor:
    async def test_it_records_the_question_then_answers_from_the_retry(self) -> None:
        from mcp_core.elicitation import ElicitAction
        from mcp_core.mrtr import InputRequiredError

        first = MrtrContext(principal="a", method="tools/call", digest="d", can_ask=True)
        with pytest.raises(InputRequiredError):
            await mrtr_elicitor(first)("Decide the gate", {"type": "object", "properties": {}})
        assert first.pending["q1"]["params"]["message"] == "Decide the gate"

        retry = MrtrContext(
            principal="a",
            method="tools/call",
            digest="d",
            responses={"q1": {"action": "accept", "content": {"decision": "approve"}}},
        )
        answer = await mrtr_elicitor(retry)("Decide the gate", {"type": "object"})
        assert answer.action is ElicitAction.ACCEPT
        assert answer.content == {"decision": "approve"}
        assert retry.pending == {}


def _asking_adapter(answers: list[Any]) -> McpToolServer:
    """A read-only tool that asks the person something, as flow.await_gate does."""
    from mcp_core.elicitation import current_elicitor

    class _Asker(McpToolServer):
        def __init__(self) -> None:
            super().__init__(adapter_id="flow", version="0.1.0")
            self.register_tool(
                ToolManifest(
                    tool_id="flow.status",
                    adapter_id="flow",
                    name="ask",
                    description="stub",
                    capability="test",
                ),
                self._handler,
            )

        async def _handler(self, args: dict[str, Any]) -> dict[str, Any]:
            elicitor = current_elicitor()
            if elicitor is None:
                return {"asked": False}
            result = await elicitor("Decide the gate", {"type": "object", "properties": {}})
            answers.append(result.content)
            return {"asked": True}

    return _Asker()


@pytest.mark.asyncio
async def test_a_tool_that_asks_gets_its_answer_on_the_retry(gateway: httpx.AsyncClient) -> None:
    answers: list[Any] = []
    async with gateway as http:
        server = UnifiedMcpServer(
            [_asking_adapter(answers)],
            caller=Caller.REMOTE,
            approval_ledger=RemoteApprovalLedger("http://gateway.test", client=http),
        )
        params = _params()
        params["name"] = "flow.status"
        params["arguments"] = {}
        first = (await _call(server, params))["result"]
        assert first["resultType"] == "input_required"
        assert first["inputRequests"]["q1"]["params"]["message"] == "Decide the gate"
        retry = {
            **params,
            "inputResponses": {"q1": {"action": "accept", "content": {"decision": "approve"}}},
            "requestState": first["requestState"],
        }
        done = (await _call(server, retry, rid=2))["result"]
    assert done["resultType"] == "complete"
    assert answers == [{"decision": "approve"}]
