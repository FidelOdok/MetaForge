"""SSE resume via per-thread event ids + ring buffer (MET-593). Network-free."""

from __future__ import annotations

import asyncio

import pytest

from api_gateway.chat.streaming import (
    ChatStreamManager,
    StreamEvent,
    StreamEventType,
    stream_thread,
)


def _ev(thread: str, delta: str) -> StreamEvent:
    return StreamEvent(event=StreamEventType.MESSAGE_DELTA, data={"delta": delta}, thread_id=thread)


@pytest.mark.asyncio
async def test_broadcast_assigns_monotonic_ids_per_thread() -> None:
    mgr = ChatStreamManager()
    for d in ("a", "b"):
        await mgr.broadcast(_ev("t1", d))
    await mgr.broadcast(_ev("t2", "x"))
    ids_t1 = [e.event_id for e in mgr.replay_since("t1", 0)]
    assert ids_t1 == [1, 2]
    assert [e.event_id for e in mgr.replay_since("t2", 0)] == [1]  # independent


@pytest.mark.asyncio
async def test_replay_returns_only_the_gap_and_ring_caps() -> None:
    mgr = ChatStreamManager()
    for i in range(300):
        await mgr.broadcast(_ev("t1", str(i)))
    gap = mgr.replay_since("t1", 297)
    assert [e.data["delta"] for e in gap] == ["297", "298", "299"]
    # Ring capped at 256 — the oldest ids are gone.
    assert len(mgr.replay_since("t1", 0)) == 256


@pytest.mark.asyncio
async def test_sse_wire_format_carries_id_line() -> None:
    mgr = ChatStreamManager()
    await mgr.broadcast(_ev("t1", "hi"))
    wire = mgr.replay_since("t1", 0)[0].to_sse()
    assert wire.startswith("id: 1\nevent: message.delta\n")


@pytest.mark.asyncio
async def test_stream_resume_replays_gap_then_live_without_duplicates() -> None:
    mgr = ChatStreamManager()
    # Client heard events 1-2, disconnected; 3-4 broadcast during the gap.
    for d in ("one", "two", "three", "four"):
        await mgr.broadcast(_ev("t1", d))

    agen = stream_thread("t1", manager=mgr, last_event_id=2)
    got: list[str] = []
    got.append(await agen.__anext__())  # replayed 3
    got.append(await agen.__anext__())  # replayed 4

    # A live event lands after resume; replayed events must not repeat.
    await mgr.broadcast(_ev("t1", "five"))
    got.append(await asyncio.wait_for(agen.__anext__(), timeout=2))
    await agen.aclose()

    assert [g.split("id: ")[1].split("\n")[0] for g in got] == ["3", "4", "5"]
    assert '"delta": "three"' in got[0] and '"delta": "five"' in got[2]


@pytest.mark.asyncio
async def test_no_resume_id_streams_live_only() -> None:
    mgr = ChatStreamManager()
    await mgr.broadcast(_ev("t1", "old"))
    agen = stream_thread("t1", manager=mgr)
    # Async generators are lazy — start the read first so the generator
    # subscribes, THEN broadcast the live event.
    task = asyncio.ensure_future(agen.__anext__())
    await asyncio.sleep(0.05)
    await mgr.broadcast(_ev("t1", "new"))
    first = await asyncio.wait_for(task, timeout=2)
    await agen.aclose()
    assert '"delta": "new"' in first  # history not replayed uninvited
