"""A held write has to reach the queue a human is watching (FORGE-406).

Two failures, stacked, and the second hid behind the first.

1. **Nothing built an approval gate outside the test suite.**
   ``build_mcp_approval_gate`` was called only from
   ``tests/unit/test_mcp_approval_queue.py``, so the sidecar ran with
   ``approval_gate=None`` and every plugin write came back "no approval gate
   is configured". FORGE-359's guardrail was present, correct and
   unreachable — tested thoroughly, wired nowhere.

2. **The approval store is process-level.** Even once wired, a call held in
   the sidecar would sit in an ``InMemoryRunStore`` in *that* process, while
   the dashboard reads the gateway's. The reviewer would never see it.

So the test that matters here drives the gate against a **real gateway app**
over HTTP, decides through the same REST endpoint the dashboard uses, and
asserts the waiting call is released with the right approver. A double for
the gateway would pass while proving nothing about the thing that was
broken — which is exactly how this shipped.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from mcp_core.guardrails import ApprovalAsk, ApprovalOutcome, Caller
from metaforge.mcp.remote_approvals import build_remote_approval_gate


@pytest.fixture
def gateway_client():
    """A real gateway app, over ASGI rather than a socket."""
    from fastapi import FastAPI

    from api_gateway.chat.tool_approvals import reset_approval_store, router

    reset_approval_store()
    app = FastAPI()
    app.include_router(router)
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://gateway.test")


def _ask(tool_id: str = "twin.record_decision") -> ApprovalAsk:
    return ApprovalAsk(
        tool_id=tool_id,
        arguments={"title": "Use aluminium"},
        caller=Caller.UNTRUSTED,
        reason="writes; held for approval (untrusted caller)",
        session_id="sess-1",
        project="proj-1",
    )


@pytest.mark.asyncio
class TestTheHeldCallReachesTheDashboardQueue:
    async def test_a_call_held_from_another_process_is_visible_to_the_gateway(
        self, gateway_client
    ) -> None:
        """The bug in one assertion: the sidecar holds, the *gateway* sees it.

        Before FORGE-406 the held call lived in the sidecar's own memory and
        the approvals page — served from the gateway — showed nothing.
        """
        async with gateway_client as http:
            gate = build_remote_approval_gate(
                "http://gateway.test", timeout_seconds=2.0, poll_interval=0.05, client=http
            )
            task = asyncio.create_task(gate(_ask()))
            await asyncio.sleep(0.2)

            pending = (await http.get("/v1/chat/tool_approvals")).json()["runs"]
            assert len(pending) == 1, "the held call never reached the gateway's queue"
            held = pending[0]
            assert held["request"]["tool"] == "twin.record_decision"
            assert held["request"]["caller"] == "untrusted"
            assert held["approval_reason"]

            task.cancel()

    async def test_approving_through_the_dashboard_endpoint_releases_the_call(
        self, gateway_client
    ) -> None:
        async with gateway_client as http:
            gate = build_remote_approval_gate(
                "http://gateway.test", timeout_seconds=5.0, poll_interval=0.05, client=http
            )
            task = asyncio.create_task(gate(_ask()))
            await asyncio.sleep(0.2)

            run_id = (await http.get("/v1/chat/tool_approvals")).json()["runs"][0]["id"]
            # The same endpoint the dashboard posts to.
            await http.post(f"/v1/chat/tool_approvals/{run_id}", json={"decision": "approve"})

            resolution = await asyncio.wait_for(task, timeout=5.0)
            assert resolution.outcome is ApprovalOutcome.APPROVED

    async def test_the_approver_comes_from_the_ledger_not_the_caller(self, gateway_client) -> None:
        """FORGE-393 across a process boundary. The sidecar never learns who
        approved except by reading it back off the record."""
        async with gateway_client as http:
            gate = build_remote_approval_gate(
                "http://gateway.test", timeout_seconds=5.0, poll_interval=0.05, client=http
            )
            task = asyncio.create_task(gate(_ask()))
            await asyncio.sleep(0.2)

            run_id = (await http.get("/v1/chat/tool_approvals")).json()["runs"][0]["id"]
            await http.post(f"/v1/chat/tool_approvals/{run_id}", json={"decision": "approve"})

            resolution = await asyncio.wait_for(task, timeout=5.0)
            assert resolution.approver is not None
            # Local gateway, auth off: an honest unverified label, never a
            # name anybody supplied.
            assert resolution.approver.actor_id == "local:dashboard"
            assert resolution.approver.verified is False

    async def test_rejecting_releases_the_call_as_rejected(self, gateway_client) -> None:
        async with gateway_client as http:
            gate = build_remote_approval_gate(
                "http://gateway.test", timeout_seconds=5.0, poll_interval=0.05, client=http
            )
            task = asyncio.create_task(gate(_ask()))
            await asyncio.sleep(0.2)
            run_id = (await http.get("/v1/chat/tool_approvals")).json()["runs"][0]["id"]
            await http.post(f"/v1/chat/tool_approvals/{run_id}", json={"decision": "reject"})

            resolution = await asyncio.wait_for(task, timeout=5.0)
            assert resolution.outcome is ApprovalOutcome.REJECTED

    async def test_an_unanswered_call_times_out_rather_than_hanging(self, gateway_client) -> None:
        async with gateway_client as http:
            gate = build_remote_approval_gate(
                "http://gateway.test", timeout_seconds=0.4, poll_interval=0.05, client=http
            )
            resolution = await gate(_ask())
            # Distinct from rejected on purpose: nobody said no, nobody said
            # anything, and those need different words to the agent.
            assert resolution.outcome is ApprovalOutcome.TIMED_OUT


@pytest.mark.asyncio
@pytest.mark.asyncio
class TestThroughTheRealDispatcher:
    async def test_a_plugin_write_is_held_and_then_runs(self, gateway_client) -> None:
        """The production path, end to end.

        Every other test here exercises the gate. This one goes through
        ``UnifiedMcpServer`` exactly as a plugin does -- an untrusted HTTP
        caller, a real tools/call -- because the bug was never in the gate.
        It was that nothing connected the gate to the dispatcher, and only a
        test that uses both would have noticed.
        """
        import json

        from metaforge.mcp.server import UnifiedMcpServer
        from tool_registry.mcp_server.handlers import ToolManifest
        from tool_registry.mcp_server.server import McpToolServer

        ran: list[str] = []

        class _Spy(McpToolServer):
            def __init__(self) -> None:
                super().__init__(adapter_id="twin", version="0.1.0")
                self.register_tool(
                    ToolManifest(
                        tool_id="twin.record_decision",
                        adapter_id="twin",
                        name="record",
                        description="stub",
                        capability="test",
                    ),
                    self._handler,
                )

            async def _handler(self, args: dict) -> dict:
                ran.append("twin.record_decision")
                return {"ok": True}

        async with gateway_client as http:
            server = UnifiedMcpServer(
                adapters=[_Spy()],
                caller=Caller.UNTRUSTED,
                approval_gate=build_remote_approval_gate(
                    "http://gateway.test",
                    timeout_seconds=5.0,
                    poll_interval=0.05,
                    client=http,
                ),
            )
            call = asyncio.create_task(
                server.handle_request(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "tools/call",
                            "params": {
                                "name": "twin.record_decision",
                                "arguments": {"title": "Use aluminium"},
                            },
                        }
                    )
                )
            )
            await asyncio.sleep(0.3)

            # It is held, not refused, and not run.
            assert ran == [], "the write ran before anyone approved it"
            pending = (await http.get("/v1/chat/tool_approvals")).json()["runs"]
            assert len(pending) == 1

            await http.post(
                f"/v1/chat/tool_approvals/{pending[0]['id']}", json={"decision": "approve"}
            )
            response = json.loads(await asyncio.wait_for(call, timeout=5.0))

        assert "error" not in response, response
        assert ran == ["twin.record_decision"]


class TestAnUnreachableLedger:
    async def test_it_refuses_rather_than_running_the_write(self) -> None:
        """If the gateway cannot be reached there is no human to ask.

        Returning APPROVED would run the write on the strength of a network
        failure — the single worst outcome available here.
        """
        gate = build_remote_approval_gate(
            "http://127.0.0.1:1", timeout_seconds=1.0, poll_interval=0.05
        )
        resolution = await gate(_ask())
        assert resolution.outcome is ApprovalOutcome.REJECTED
        assert resolution.approver is None


class TestTheGateIsActuallyWired:
    def test_the_sidecar_builds_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The first half of the bug, stated directly: the production path
        has to produce a gate, not just be capable of one."""
        from metaforge.mcp.__main__ import _build_approval_gate

        monkeypatch.setenv("METAFORGE_GATEWAY_URL", "http://gateway.test")
        assert _build_approval_gate() is not None

    def test_it_prefers_the_gateway_ledger_over_its_own(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The second half. An in-process queue in the sidecar is a queue
        nobody can see, so a configured gateway must win."""
        from structlog.testing import capture_logs

        from metaforge.mcp.__main__ import _build_approval_gate

        monkeypatch.setenv("METAFORGE_GATEWAY_URL", "http://gateway.test")
        with capture_logs() as logs:
            _build_approval_gate()
        events = {entry.get("event") for entry in logs}
        assert "mcp_approval_gate_remote" in events
        assert "mcp_approval_gate_in_process" not in events

    def test_falling_back_to_the_in_process_queue_warns(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Correct inside the gateway, wrong in a sidecar — and the
        difference is invisible at runtime unless it is said out loud."""
        from structlog.testing import capture_logs

        from metaforge.mcp.__main__ import _build_approval_gate

        monkeypatch.delenv("METAFORGE_GATEWAY_URL", raising=False)
        with capture_logs() as logs:
            gate = _build_approval_gate()
        assert gate is not None
        warning = next(e for e in logs if e.get("event") == "mcp_approval_gate_in_process")
        assert "never appear on the dashboard" in warning["detail"]
