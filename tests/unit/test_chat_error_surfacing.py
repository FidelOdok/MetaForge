"""A failed harness turn must put its CAUSE on the stream, not just end.

`notify_error` existed in `api_gateway/chat/streaming.py` with a unit test,
and the forge TUI has always handled the `error` event (`useChat.ts` sets
`stats.errored` + `errorMsg`) — but nothing in the codebase ever CALLED it.
So a real provider failure reached the user as the generic

    (no reply — the agent produced no output (exhausted, or an empty answer))

with the actual reason (a 400 for an over-long tools array) visible only in
gateway logs. The error ChatMessageRecord the route returns doesn't help: the
TUI renders streamed assistant deltas, not the returned record.
"""

from __future__ import annotations

import pytest

from api_gateway.chat import routes as chat_routes
from api_gateway.chat.models import ChatThreadRecord
from api_gateway.chat.streaming import StreamEventType, stream_manager

_BOOM = (
    "all providers failed for role 'generator': openrouter:openai/gpt-4o-mini -> "
    "Error code: 400 - Invalid 'tools': array too long. Expected an array with "
    "maximum length 128, but got an array with length 130 instead."
)


def _thread() -> ChatThreadRecord:
    from datetime import UTC, datetime
    from uuid import uuid4

    now = datetime.now(UTC)
    return ChatThreadRecord(
        id=str(uuid4()),
        title="t",
        scope_kind="assistant",
        scope_entity_id="s",
        channel_id="c",
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def harness_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chat_routes, "chat_harness_enabled", lambda: True)


async def test_harness_failure_emits_an_error_event_with_the_cause(
    harness_on: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    thread = _thread()
    queue = stream_manager.subscribe(thread.id)

    async def _boom(*args: object, **kwargs: object) -> str:
        raise RuntimeError(_BOOM)

    monkeypatch.setattr(chat_routes, "run_chat_turn_streaming", _boom)

    try:
        await chat_routes._invoke_agent(thread, "hello")
        events = []
        while not queue.empty():
            events.append(queue.get_nowait())
    finally:
        stream_manager.unsubscribe(thread.id, queue)

    errors = [e for e in events if e.event == StreamEventType.ERROR]
    assert errors, f"no error event emitted; got {[e.event for e in events]}"
    # The cause must survive to the client, not be flattened to "failed".
    assert "array too long" in errors[0].data["error"]
    assert "128" in errors[0].data["error"]


async def test_turn_still_ends_after_the_error_event(
    harness_on: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MET-591's agent.done must still fire — otherwise the UI reads the turn
    as hung rather than failed. The new error event is additive."""
    thread = _thread()
    queue = stream_manager.subscribe(thread.id)

    async def _boom(*args: object, **kwargs: object) -> str:
        raise RuntimeError(_BOOM)

    monkeypatch.setattr(chat_routes, "run_chat_turn_streaming", _boom)

    try:
        await chat_routes._invoke_agent(thread, "hello")
        events = []
        while not queue.empty():
            events.append(queue.get_nowait())
    finally:
        stream_manager.unsubscribe(thread.id, queue)

    kinds = [e.event for e in events]
    assert StreamEventType.ERROR in kinds
    assert StreamEventType.AGENT_DONE in kinds


async def test_error_notification_failure_does_not_break_the_turn(
    harness_on: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Notification is best-effort — a broken stream must not replace the
    provider error with a notification error."""
    thread = _thread()

    async def _boom(*args: object, **kwargs: object) -> str:
        raise RuntimeError(_BOOM)

    async def _bad_notify(*args: object, **kwargs: object) -> int:
        raise RuntimeError("stream gone")

    monkeypatch.setattr(chat_routes, "run_chat_turn_streaming", _boom)
    monkeypatch.setattr(chat_routes, "notify_error", _bad_notify)

    record = await chat_routes._invoke_agent(thread, "hello")
    assert record is not None
    assert record.status == "error"
    assert "array too long" in record.content
