"""A held write tells its client it is held, and ends before the client does (FORGE-465).

Two ``project.create`` calls were held for the dashboard. Claude Code's tool
timeout (120 s) ran out before the hold window (180 s), the agent saw only
"tool timed out after 120s", and told the user MetaForge never asked for
anything. These tests pin the three parts of the fix: an early progress
notification naming the approval, a window below the client's timeout when
no progress can be sent, and a refusal that names its outcome and approval.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from api_gateway.chat.tool_approvals import get_approval_store, reset_approval_store
from api_gateway.mcp_approvals import build_mcp_approval_gate
from mcp_core.approval_hold import (
    DEFAULT_HOLD_SECONDS,
    DEFAULT_HOLD_WITH_PROGRESS_SECONDS,
    ENV_HOLD_SECONDS,
    hold_window_seconds,
)
from mcp_core.guardrails import (
    ApprovalAsk,
    ApprovalOutcome,
    ApprovalRejectedError,
    ApprovalResolution,
    Caller,
)
from metaforge.mcp.__main__ import build_http_app
from metaforge.mcp.server import UnifiedMcpServer
from orchestrator.harness.runs import ApprovalDecision, RunStatus
from tests.unit.test_http_elicitation_call_stream import _Client, _initialize, _Projects

#: Claude Code's MCP tool timeout, which the incident ran into.
CLIENT_TOOL_TIMEOUT = 120.0


class _Notifier:
    """Collects what the server would have sent to the client."""

    def __init__(self, *, available: bool = True) -> None:
        self._available = available
        self.sent: list[dict[str, Any]] = []

    def available(self) -> bool:
        return self._available

    def send(self, message: dict[str, Any]) -> bool:
        self.sent.append(message)
        return True


@pytest.fixture(autouse=True)
def _clean_store():
    reset_approval_store()
    yield
    reset_approval_store()


def _create(*, progress_token: str | None = None, call_id: int = 1) -> dict[str, Any]:
    params: dict[str, Any] = {"name": "project.create", "arguments": {"name": "drone"}}
    if progress_token is not None:
        params["_meta"] = {"progressToken": progress_token}
    return {"jsonrpc": "2.0", "id": call_id, "method": "tools/call", "params": params}


def _server(gate: Any, *, dashboard_url: str | None = None) -> tuple[UnifiedMcpServer, _Projects]:
    projects = _Projects()
    server = UnifiedMcpServer(
        [projects], caller=Caller.UNTRUSTED, approval_gate=gate, dashboard_url=dashboard_url
    )
    return server, projects


async def _pending_id(timeout: float = 5.0) -> str:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        for run in get_approval_store().list():
            if run.status is RunStatus.AWAITING_APPROVAL:
                return run.id
        await asyncio.sleep(0.02)
    raise AssertionError("no hold ever reached awaiting_approval")


class _RecordingGate:
    """Records the ask and answers with a fixed resolution."""

    def __init__(self, resolution: ApprovalResolution) -> None:
        self.resolution = resolution
        self.asks: list[ApprovalAsk] = []

    async def __call__(self, ask: ApprovalAsk) -> ApprovalResolution:
        self.asks.append(ask)
        return self.resolution


@pytest.mark.asyncio
class TestTheEarlySignal:
    async def test_progress_naming_the_approval_arrives_within_a_second(self) -> None:
        server, projects = _server(
            build_mcp_approval_gate(poll_interval=0.02), dashboard_url="http://dash.test"
        )
        notifier = _Notifier()
        server.attach_notifier(notifier)

        call = asyncio.create_task(server.handle_request(json.dumps(_create(progress_token="t1"))))
        approval_id = await _pending_id()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 1.0
        while not notifier.sent and loop.time() < deadline:
            await asyncio.sleep(0.01)

        assert notifier.sent, "no progress notification within 1 s of the hold"
        first = notifier.sent[0]
        assert first["method"] == "notifications/progress"
        assert first["params"]["progressToken"] == "t1"
        assert approval_id in first["params"]["message"]
        assert "http://dash.test/approvals" in first["params"]["message"]
        # With progress the long window is kept, and recorded on the hold.
        assert first["params"]["total"] == DEFAULT_HOLD_WITH_PROGRESS_SECONDS
        assert projects.calls == []

        get_approval_store().submit_approval(approval_id, ApprovalDecision.APPROVE)
        response = json.loads(await asyncio.wait_for(call, 5.0))
        assert "error" not in response, response
        assert projects.calls == [{"name": "drone"}]

    async def test_progress_repeats_while_the_call_waits(self, monkeypatch) -> None:
        monkeypatch.setenv("METAFORGE_APPROVAL_PROGRESS_INTERVAL_SECONDS", "0.05")
        server, _ = _server(build_mcp_approval_gate(poll_interval=0.02))
        notifier = _Notifier()
        server.attach_notifier(notifier)

        call = asyncio.create_task(server.handle_request(json.dumps(_create(progress_token=7))))
        approval_id = await _pending_id()
        await asyncio.sleep(0.3)
        get_approval_store().submit_approval(approval_id, ApprovalDecision.REJECT)
        await asyncio.wait_for(call, 5.0)

        progress = [m["params"]["progress"] for m in notifier.sent]
        assert len(progress) >= 3, notifier.sent
        assert progress == sorted(progress) and len(set(progress)) == len(progress), (
            "progress must increase with every notification"
        )
        count = len(notifier.sent)
        await asyncio.sleep(0.15)
        assert len(notifier.sent) == count, "progress kept coming after the call ended"

    async def test_no_progress_token_means_nothing_is_sent(self) -> None:
        gate = _RecordingGate(ApprovalResolution(ApprovalOutcome.APPROVED, approval_id="run_x"))
        server, _ = _server(gate)
        notifier = _Notifier()
        server.attach_notifier(notifier)
        await server.handle_request(json.dumps(_create()))
        assert notifier.sent == []


@pytest.mark.asyncio
class TestTheWindow:
    async def test_without_a_progress_channel_the_window_is_below_client_timeouts(self) -> None:
        gate = _RecordingGate(ApprovalResolution(ApprovalOutcome.APPROVED, approval_id="run_x"))
        server, _ = _server(gate)
        # A progress token, but no transport to carry it.
        await server.handle_request(json.dumps(_create(progress_token="t")))
        assert gate.asks[0].timeout_seconds == DEFAULT_HOLD_SECONDS
        assert DEFAULT_HOLD_SECONDS < CLIENT_TOOL_TIMEOUT

    async def test_a_channel_that_cannot_deliver_now_is_no_channel(self) -> None:
        gate = _RecordingGate(ApprovalResolution(ApprovalOutcome.APPROVED, approval_id="run_x"))
        server, _ = _server(gate)
        server.attach_notifier(_Notifier(available=False))
        await server.handle_request(json.dumps(_create(progress_token="t")))
        assert gate.asks[0].timeout_seconds == DEFAULT_HOLD_SECONDS

    async def test_the_chosen_window_is_the_hold_deadline(self) -> None:
        server, _ = _server(build_mcp_approval_gate(poll_interval=0.02))
        call = asyncio.create_task(server.handle_request(json.dumps(_create())))
        approval_id = await _pending_id()
        run = get_approval_store().get(approval_id)
        assert run.approval_deadline is not None
        remaining = run.approval_deadline - get_approval_store().now()
        # Window plus the gateway's grace, not the old fixed 180 s.
        assert DEFAULT_HOLD_SECONDS < remaining <= DEFAULT_HOLD_SECONDS + 31
        call.cancel()
        with pytest.raises(asyncio.CancelledError):
            await call


@pytest.mark.asyncio
class TestTheOutcome:
    async def test_an_unapproved_timeout_names_timed_out_and_the_approval(self) -> None:
        server, projects = _server(
            build_mcp_approval_gate(timeout_seconds=0.2, poll_interval=0.02),
            dashboard_url="http://dash.test",
        )
        response = json.loads(
            await asyncio.wait_for(server.handle_request(json.dumps(_create())), 5.0)
        )
        error = response["error"]
        approval_id = error["data"]["approval_id"]
        assert approval_id.startswith("run_")
        assert get_approval_store().get(approval_id).status is RunStatus.TIMED_OUT
        assert error["data"]["outcome"] == "timed_out"
        assert error["data"]["route"] == "dashboard"
        # What Claude Code shows is the message, so the facts live there too.
        assert "timed_out" in error["message"]
        assert approval_id in error["message"]
        assert "http://dash.test/approvals" in error["message"]
        assert projects.calls == []

    @pytest.mark.parametrize(
        ("outcome", "named"),
        [
            (ApprovalOutcome.REJECTED, "a reviewer rejected it"),
            (ApprovalOutcome.CANCELLED, "cancelled before anyone answered"),
        ],
    )
    async def test_rejected_and_cancelled_are_named(
        self, outcome: ApprovalOutcome, named: str
    ) -> None:
        gate = _RecordingGate(ApprovalResolution(outcome, approval_id="run_abc"))
        server, _ = _server(gate)
        response = json.loads(await server.handle_request(json.dumps(_create())))
        assert response["error"]["data"]["outcome"] == outcome.value
        assert f"({outcome.value})" in response["error"]["message"]
        assert "run_abc" in response["error"]["message"]
        assert named in response["error"]["message"]


@pytest.mark.asyncio
class TestOverHttp:
    async def test_the_progress_arrives_on_the_calls_own_stream(self, monkeypatch) -> None:
        """A client that cannot elicit still gets the notice, on its POST's stream."""
        monkeypatch.setenv("METAFORGE_APPROVAL_PROGRESS_INTERVAL_SECONDS", "0.1")
        projects = _Projects()
        server = UnifiedMcpServer(
            [projects],
            caller=Caller.UNTRUSTED,
            approval_gate=build_mcp_approval_gate(poll_interval=0.02),
        )
        client = _Client(build_http_app(server, enable_sse=False))
        session = await _initialize(client, elicitation=False)

        call = client.post(_create(progress_token="p-1"), session=session)
        await call.start()
        assert call.status == 200
        assert call.headers["content-type"].startswith("text/event-stream")

        notice = await asyncio.wait_for(call.next_event(), 1.0)
        assert notice is not None and notice["method"] == "notifications/progress"
        approval_id = await _pending_id()
        assert approval_id in notice["params"]["message"]
        assert notice["params"]["progressToken"] == "p-1"
        assert notice["params"]["total"] == DEFAULT_HOLD_WITH_PROGRESS_SECONDS

        again = await asyncio.wait_for(call.next_event(), 2.0)
        assert again is not None and again["method"] == "notifications/progress"
        assert again["params"]["progress"] > notice["params"]["progress"]

        get_approval_store().submit_approval(approval_id, ApprovalDecision.APPROVE)
        while True:
            event = await asyncio.wait_for(call.next_event(), 5.0)
            assert event is not None, "the stream ended without the result"
            if event.get("id") == 1:
                break
        assert "result" in event
        assert approval_id in json.dumps(event)
        assert projects.calls == [{"name": "drone"}]

    async def test_without_a_progress_token_a_non_eliciting_client_keeps_json(self) -> None:
        server = UnifiedMcpServer(
            [_Projects()],
            caller=Caller.UNTRUSTED,
            approval_gate=build_mcp_approval_gate(timeout_seconds=0.2, poll_interval=0.02),
        )
        client = _Client(build_http_app(server, enable_sse=False))
        session = await _initialize(client, elicitation=False)
        exchange, body = await client.post_json(_create(), session=session)
        assert exchange.headers["content-type"].startswith("application/json")
        assert body["error"]["data"]["outcome"] == "timed_out"


def test_the_window_is_configurable(monkeypatch) -> None:
    monkeypatch.setenv(ENV_HOLD_SECONDS, "45")
    assert hold_window_seconds(progress=False) == 45.0
    monkeypatch.setenv(ENV_HOLD_SECONDS, "not-a-number")
    assert hold_window_seconds(progress=False) == DEFAULT_HOLD_SECONDS
    monkeypatch.setenv("METAFORGE_APPROVAL_HOLD_PROGRESS_SECONDS", "600")
    assert hold_window_seconds(progress=True) == 600.0


def test_the_message_reads_as_a_sentence() -> None:
    exc = ApprovalRejectedError(
        "project.create",
        ApprovalOutcome.TIMED_OUT,
        approval_id="run_1",
        where="the MetaForge dashboard Approvals page",
        window_seconds=100.0,
    )
    assert str(exc) == (
        "project.create was not run (timed_out): held for approval run_1 on the "
        "MetaForge dashboard Approvals page, and no one answered before the "
        "approval window closed (100s)."
    )
