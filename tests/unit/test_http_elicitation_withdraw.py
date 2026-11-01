"""A question the server stopped waiting on is withdrawn (FORGE-472).

Live, `flow.propose` from Claude Code 2.1.287 was held on the call's own
stream (FORGE-464) and timed out server-side after 180 s, yet Claude Code
still showed the Accept/Decline form ten minutes later: nothing told it the
question was over. The 180 s window also outlived Claude Code's 120 s tool
timeout, and Claude Code sends no progressToken, so FORGE-465's keep-alive
could not help. These drive the real sidecar app over raw ASGI, like the
FORGE-464 tests they extend.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
import structlog

from mcp_core.approval_hold import DEFAULT_HOLD_SECONDS, ENV_HOLD_SECONDS
from mcp_core.guardrails import ApprovalAsk, ApprovalOutcome, Caller
from metaforge.mcp.http_elicitation import ElicitationHub
from tests.unit.test_http_elicitation_call_stream import (
    _answer,
    _app,
    _create,
    _initialize,
    _tool_text,
)


@pytest.mark.asyncio
class TestAnExpiredQuestionIsWithdrawn:
    async def test_a_timeout_sends_cancelled_with_the_request_id_before_the_result(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ENV_HOLD_SECONDS, "0.3")
        client, projects = _app()
        session = await _initialize(client)
        call = client.post(_create(), session=session)
        await call.start()

        ask = await call.next_event()
        assert ask is not None and ask["method"] == "elicitation/create"

        cancelled = await call.next_event()
        assert cancelled is not None
        assert cancelled["method"] == "notifications/cancelled"
        assert cancelled["params"]["requestId"] == ask["id"]
        assert cancelled["params"]["reason"]

        # The withdrawal comes first, then the call's result, then the end.
        result = await call.next_event()
        assert result is not None and result["id"] == 1
        assert "timed_out" in _tool_text(result)
        assert await call.next_event() is None
        assert projects.calls == []

    async def test_a_late_answer_is_ignored_and_never_applied(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ENV_HOLD_SECONDS, "0.3")
        client, projects = _app()
        session = await _initialize(client)
        call = client.post(_create(), session=session)
        await call.start()
        ask = await call.next_event()
        assert ask is not None
        # Drain the withdrawal and the timed-out result; the call is over.
        while await call.next_event() is not None:
            pass

        with structlog.testing.capture_logs() as logs:
            late, body = await client.post_json(
                _answer(ask, {"action": "accept", "content": {"approve": True}}),
                session=session,
            )
        # Consumed, not dispatched as a request and not applied to anything.
        assert late.status == 202
        assert body is None
        assert projects.calls == []
        assert any(
            entry["event"] == "mcp_elicitation_late_response_ignored"
            and entry["message_id"] == ask["id"]
            for entry in logs
        )


@pytest.mark.asyncio
class TestTheInlineWindowFitsTheClient:
    async def test_without_progress_the_window_is_the_short_default_and_is_said(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(ENV_HOLD_SECONDS, raising=False)
        client, projects = _app()
        session = await _initialize(client)
        with structlog.testing.capture_logs() as logs:
            call = client.post(_create(), session=session)
            await call.start()
            ask = await call.next_event()
            assert ask is not None
            await client.post_json(
                _answer(ask, {"action": "accept", "content": {"approve": True}}),
                session=session,
            )
            result = await call.next_event()
        assert result is not None and "error" not in result
        assert projects.calls == [{"name": "drone"}]

        assert DEFAULT_HOLD_SECONDS == 100.0
        assert "Answer within 100 seconds" in ask["params"]["message"]
        held = [e for e in logs if e["event"] == "mcp_tool_call_held_for_approval"]
        assert held and held[0]["route"] == "elicitation"
        assert held[0]["window_seconds"] == 100.0
        assert held[0]["progress"] is False


@pytest.mark.asyncio
class TestTheHubDirectly:
    async def test_a_cancelled_wait_withdraws_on_the_session_stream(self) -> None:
        """A cancelled call (not only a timeout) takes the form down too."""
        hub = ElicitationHub()
        hub.note_initialize(
            "s-1", capabilities={"elicitation": {}}, negotiated_protocol="2025-06-18"
        )
        frames = hub.stream("s-1")
        await frames.__anext__()  # the open comment
        task = asyncio.create_task(hub.elicit("s-1", "q?", {"type": "object"}))
        sent = (await frames.__anext__()).decode()
        assert "elicitation/create" in sent
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        withdrawn = (await frames.__anext__()).decode()
        assert "notifications/cancelled" in withdrawn
        assert '"requestId": "elicit-s-1-1"' in withdrawn
        await frames.aclose()

    async def test_an_unknown_response_is_still_left_to_the_transport(self) -> None:
        hub = ElicitationHub()
        assert hub.resolve({"jsonrpc": "2.0", "id": "nobody-asked", "result": {}}) is False


@pytest.mark.asyncio
async def test_the_gate_ends_at_the_ask_window() -> None:
    from mcp_core.elicitation import ElicitResult, elicitation_gate

    async def never(message: str, schema: dict[str, Any]) -> ElicitResult:
        await asyncio.sleep(60)
        raise AssertionError("unreachable")

    ask = ApprovalAsk(
        tool_id="project.create",
        arguments={},
        caller=Caller.UNTRUSTED,
        reason="held",
        timeout_seconds=0.05,
    )
    assert await elicitation_gate(never)(ask) is ApprovalOutcome.TIMED_OUT
