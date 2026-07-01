"""Unit tests for run SSE streaming (MET-547, Phase 1)."""

from __future__ import annotations

import asyncio

import pytest

from api_gateway.runs.streaming import (
    RunStreamManager,
    run_event_stream,
    run_event_to_sse,
)
from orchestrator.harness.runs import ApprovalDecision, InMemoryRunStore


def _clocked_store(manager: RunStreamManager) -> InMemoryRunStore:
    ticks = iter(range(1, 10_000))
    return InMemoryRunStore(
        clock=lambda: float(next(ticks)),
        on_transition=manager.publish,
    )


def test_sse_frame_format() -> None:
    manager = RunStreamManager()
    store = _clocked_store(manager)
    run = store.create({}, run_id="r1")
    frame = run_event_to_sse(run)
    assert frame.startswith("event: run.status\n")
    assert '"id": "r1"' in frame
    assert '"status": "queued"' in frame
    assert frame.endswith("\n\n")


def test_manager_publishes_to_subscribers() -> None:
    manager = RunStreamManager()
    store = _clocked_store(manager)
    run = store.create({}, run_id="r1")
    queue = manager.subscribe("r1")
    store.start("r1")  # fires on_transition -> publish
    pushed = queue.get_nowait()
    assert pushed is store.get("r1")
    assert pushed.status.value == "running"
    # A run with no subscribers doesn't raise.
    store2_run = run
    manager.publish(store2_run)


def test_unsubscribe_removes_queue() -> None:
    manager = RunStreamManager()
    queue = manager.subscribe("r1")
    manager.unsubscribe("r1", queue)
    # After unsubscribe, publishing reaches nobody (no error).
    assert "r1" not in manager._subscribers


@pytest.mark.asyncio
async def test_stream_snapshot_then_terminal() -> None:
    manager = RunStreamManager()
    store = _clocked_store(manager)
    store.create({}, run_id="r1")
    store.start("r1")
    snapshot = store.get("r1")

    frames: list[str] = []

    async def consume() -> None:
        async for frame in run_event_stream("r1", snapshot, manager):
            frames.append(frame)

    task = asyncio.create_task(consume())
    await asyncio.sleep(0)  # let the generator yield the snapshot + subscribe

    store.complete("r1", result={"ok": True})  # terminal -> stream should end
    await asyncio.wait_for(task, timeout=2.0)

    assert len(frames) == 2  # snapshot (running) + terminal (completed)
    assert '"status": "running"' in frames[0]
    assert '"status": "completed"' in frames[1]


@pytest.mark.asyncio
async def test_stream_ends_immediately_if_already_terminal() -> None:
    manager = RunStreamManager()
    store = _clocked_store(manager)
    store.create({}, run_id="r1")
    store.start("r1")
    store.request_approval("r1")
    store.submit_approval("r1", ApprovalDecision.REJECT)  # rejected = terminal
    snapshot = store.get("r1")

    frames = [f async for f in run_event_stream("r1", snapshot, manager)]
    assert len(frames) == 1  # just the terminal snapshot
    assert '"status": "rejected"' in frames[0]
