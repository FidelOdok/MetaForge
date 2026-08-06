"""Unit tests for chat SSE streaming (MET-219).

Tests cover the ``ChatStreamManager``, ``StreamEvent`` model,
convenience helpers, the async ``stream_thread`` generator,
and the SSE route handler.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from api_gateway.chat.streaming import (
    ChatStreamManager,
    StreamEvent,
    StreamEventType,
    notify_agent_done,
    notify_agent_typing,
    notify_error,
    notify_new_message,
    stream_thread,
)

# ---------------------------------------------------------------------------
# StreamEvent model
# ---------------------------------------------------------------------------


class TestStreamEvent:
    """Tests for the StreamEvent Pydantic model."""

    def test_to_sse_format(self) -> None:
        """to_sse() returns properly formatted SSE text."""
        event = StreamEvent(
            event=StreamEventType.MESSAGE_CREATED,
            data={"id": "msg-1", "content": "hello"},
            thread_id="thread-1",
        )
        sse = event.to_sse()

        assert sse.startswith("event: message.created\n")
        assert "data: " in sse
        assert sse.endswith("\n\n")

        # Parse the data line
        lines = sse.strip().split("\n")
        data_line = lines[1]
        assert data_line.startswith("data: ")
        payload = json.loads(data_line[len("data: ") :])
        assert payload["data"]["id"] == "msg-1"
        assert payload["data"]["content"] == "hello"
        assert payload["thread_id"] == "thread-1"
        assert "timestamp" in payload

    def test_event_types(self) -> None:
        """All event types produce valid SSE strings."""
        for event_type in StreamEventType:
            event = StreamEvent(
                event=event_type,
                data={"test": True},
                thread_id="t-1",
            )
            sse = event.to_sse()
            assert f"event: {event_type.value}\n" in sse


# ---------------------------------------------------------------------------
# ChatStreamManager
# ---------------------------------------------------------------------------


class TestChatStreamManager:
    """Tests for subscription management and broadcasting."""

    def test_subscribe_creates_queue(self) -> None:
        """subscribe() returns a queue and tracks the connection."""
        mgr = ChatStreamManager()
        queue = mgr.subscribe("thread-1")

        assert isinstance(queue, asyncio.Queue)
        assert mgr.connection_count("thread-1") == 1

    def test_unsubscribe_removes_queue(self) -> None:
        """unsubscribe() removes the queue and cleans up empty thread entries."""
        mgr = ChatStreamManager()
        queue = mgr.subscribe("thread-1")
        mgr.unsubscribe("thread-1", queue)

        assert mgr.connection_count("thread-1") == 0
        assert "thread-1" not in mgr.active_threads()

    def test_unsubscribe_nonexistent_is_safe(self) -> None:
        """unsubscribe() on an unknown queue does not raise."""
        mgr = ChatStreamManager()
        queue: asyncio.Queue[StreamEvent | None] = asyncio.Queue()
        mgr.unsubscribe("nonexistent", queue)  # should not raise

    def test_multiple_subscribers(self) -> None:
        """Multiple clients can subscribe to the same thread."""
        mgr = ChatStreamManager()
        q1 = mgr.subscribe("thread-1")
        q2 = mgr.subscribe("thread-1")

        assert mgr.connection_count("thread-1") == 2

        mgr.unsubscribe("thread-1", q1)
        assert mgr.connection_count("thread-1") == 1

        mgr.unsubscribe("thread-1", q2)
        assert mgr.connection_count("thread-1") == 0

    @pytest.mark.asyncio
    async def test_broadcast_delivers_to_all(self) -> None:
        """broadcast() pushes events to every subscriber on that thread."""
        mgr = ChatStreamManager()
        q1 = mgr.subscribe("thread-1")
        q2 = mgr.subscribe("thread-1")
        q_other = mgr.subscribe("thread-2")

        event = StreamEvent(
            event=StreamEventType.MESSAGE_CREATED,
            data={"content": "hi"},
            thread_id="thread-1",
        )
        count = await mgr.broadcast(event)

        assert count == 2
        assert not q1.empty()
        assert not q2.empty()
        assert q_other.empty()  # different thread

        received_1 = q1.get_nowait()
        assert received_1.data["content"] == "hi"

    @pytest.mark.asyncio
    async def test_broadcast_no_subscribers(self) -> None:
        """broadcast() returns 0 when no one is listening."""
        mgr = ChatStreamManager()
        event = StreamEvent(
            event=StreamEventType.AGENT_DONE,
            data={},
            thread_id="thread-absent",
        )
        count = await mgr.broadcast(event)
        assert count == 0

    @pytest.mark.asyncio
    async def test_close_all_sends_sentinel(self) -> None:
        """close_all() sends None to all subscribers and removes them."""
        mgr = ChatStreamManager()
        q1 = mgr.subscribe("thread-1")
        q2 = mgr.subscribe("thread-1")

        await mgr.close_all("thread-1")

        assert q1.get_nowait() is None
        assert q2.get_nowait() is None
        assert mgr.connection_count("thread-1") == 0

    def test_active_threads(self) -> None:
        """active_threads() returns only threads with subscribers."""
        mgr = ChatStreamManager()
        mgr.subscribe("a")
        mgr.subscribe("b")

        threads = mgr.active_threads()
        assert set(threads) == {"a", "b"}


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------


class TestConvenienceHelpers:
    """Tests for notify_* helper functions."""

    @pytest.mark.asyncio
    async def test_notify_new_message(self) -> None:
        """notify_new_message() broadcasts a message.created event."""
        mgr = ChatStreamManager()
        queue = mgr.subscribe("t-1")

        # Temporarily patch the module-level singleton
        import api_gateway.chat.streaming as mod

        original = mod.stream_manager
        mod.stream_manager = mgr
        try:
            count = await notify_new_message("t-1", {"id": "m1", "content": "test"})
            assert count == 1
            event = queue.get_nowait()
            assert event.event == StreamEventType.MESSAGE_CREATED
            assert event.data["id"] == "m1"
        finally:
            mod.stream_manager = original

    @pytest.mark.asyncio
    async def test_notify_agent_typing(self) -> None:
        """notify_agent_typing() broadcasts an agent.typing event."""
        mgr = ChatStreamManager()
        queue = mgr.subscribe("t-2")

        import api_gateway.chat.streaming as mod

        original = mod.stream_manager
        mod.stream_manager = mgr
        try:
            count = await notify_agent_typing("t-2", agent_id="mech")
            assert count == 1
            event = queue.get_nowait()
            assert event.event == StreamEventType.AGENT_TYPING
            assert event.data["agent_id"] == "mech"
        finally:
            mod.stream_manager = original

    @pytest.mark.asyncio
    async def test_notify_agent_done(self) -> None:
        """notify_agent_done() broadcasts an agent.done event."""
        mgr = ChatStreamManager()
        queue = mgr.subscribe("t-3")

        import api_gateway.chat.streaming as mod

        original = mod.stream_manager
        mod.stream_manager = mgr
        try:
            await notify_agent_done("t-3", agent_id="ee")
            event = queue.get_nowait()
            assert event.event == StreamEventType.AGENT_DONE
        finally:
            mod.stream_manager = original

    @pytest.mark.asyncio
    async def test_notify_error(self) -> None:
        """notify_error() broadcasts an error event."""
        mgr = ChatStreamManager()
        queue = mgr.subscribe("t-4")

        import api_gateway.chat.streaming as mod

        original = mod.stream_manager
        mod.stream_manager = mgr
        try:
            await notify_error("t-4", "something broke")
            event = queue.get_nowait()
            assert event.event == StreamEventType.ERROR
            assert event.data["error"] == "something broke"
        finally:
            mod.stream_manager = original


# ---------------------------------------------------------------------------
# stream_thread async generator
# ---------------------------------------------------------------------------


class TestStreamThread:
    """Tests for the stream_thread async generator."""

    @pytest.mark.asyncio
    async def test_yields_sse_formatted_strings(self) -> None:
        """stream_thread() yields SSE-formatted strings from queued events."""
        mgr = ChatStreamManager()

        async def _produce() -> None:
            await asyncio.sleep(0.01)
            await mgr.broadcast(
                StreamEvent(
                    event=StreamEventType.MESSAGE_CREATED,
                    data={"content": "hello"},
                    thread_id="t-1",
                )
            )
            await asyncio.sleep(0.01)
            await mgr.close_all("t-1")

        task = asyncio.create_task(_produce())

        results: list[str] = []
        async for chunk in stream_thread("t-1", manager=mgr):
            results.append(chunk)

        await task

        assert len(results) == 1
        # MET-593: broadcast events carry a resume id line before the event line.
        assert results[0].startswith("id: ")
        assert "event: message.created\n" in results[0]

    @pytest.mark.asyncio
    async def test_stops_on_sentinel(self) -> None:
        """stream_thread() stops when it receives a None sentinel."""
        mgr = ChatStreamManager()

        async def _close() -> None:
            await asyncio.sleep(0.01)
            await mgr.close_all("t-2")

        task = asyncio.create_task(_close())

        chunks: list[str] = []
        async for chunk in stream_thread("t-2", manager=mgr):
            chunks.append(chunk)

        await task
        # Generator completed without hanging
        assert chunks == []

    @pytest.mark.asyncio
    async def test_cleanup_on_cancel(self) -> None:
        """stream_thread() unsubscribes when the generator is cancelled."""
        mgr = ChatStreamManager()

        gen = stream_thread("t-3", manager=mgr)
        # Start the generator (subscribes)
        assert mgr.connection_count("t-3") == 0

        # Actually start iterating to trigger subscription
        async def _iterate() -> None:
            async for _ in gen:
                pass

        task = asyncio.create_task(_iterate())
        await asyncio.sleep(0.01)
        assert mgr.connection_count("t-3") == 1

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # Give cleanup a moment
        await asyncio.sleep(0.01)
        assert mgr.connection_count("t-3") == 0
