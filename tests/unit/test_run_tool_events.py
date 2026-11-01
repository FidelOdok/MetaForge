"""Tool started/finished events on a run's stream (FORGE-643, FORGE-644)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_gateway.runs import routes as run_routes
from api_gateway.runs.streaming import (
    SUBSCRIBER_QUEUE_LIMIT,
    RunStreamManager,
    ToolEvent,
    run_event_stream,
    tool_event_to_sse,
)
from api_gateway.runs.tool_events import ScopedPhaseBrain
from orchestrator.harness.runs import InMemoryRunStore
from orchestrator.harness.tool_events import report_tool_event, tool_event_scope


def _store(manager: RunStreamManager) -> InMemoryRunStore:
    ticks = iter(range(1, 10_000))
    return InMemoryRunStore(clock=lambda: float(next(ticks)), on_transition=manager.publish)


def _frames(chunks: list[str]) -> list[tuple[str, dict[str, Any], str | None]]:
    out = []
    for chunk in chunks:
        lines = chunk.strip().split("\n")
        ident = next((ln[4:] for ln in lines if ln.startswith("id: ")), None)
        event = next(ln[7:] for ln in lines if ln.startswith("event: "))
        data = json.loads(next(ln[6:] for ln in lines if ln.startswith("data: ")))
        out.append((event, data, ident))
    return out


def test_tool_event_frame_has_id_and_no_arguments() -> None:
    frame = tool_event_to_sse(
        ToolEvent(
            run_id="r1",
            seq=3,
            call_id="c",
            tool="cadquery.execute_script",
            phase="design",
            status="started",
        )
    )
    assert frame.startswith("id: 3\nevent: tool.progress\n")
    data = json.loads(frame.split("data: ", 1)[1])
    assert set(data) == {"run_id", "seq", "call_id", "tool", "phase", "status", "duration_ms"}


def test_sequence_numbers_increase_per_run() -> None:
    manager = RunStreamManager()
    a = manager.publish_tool("r1", call_id="1", tool="t", phase="p", status="started")
    b = manager.publish_tool("r1", call_id="1", tool="t", phase="p", status="finished")
    other = manager.publish_tool("r2", call_id="1", tool="t", phase="p", status="started")
    assert (a.seq, b.seq, other.seq) == (1, 2, 1)


def test_full_subscriber_queue_drops_oldest_tool_event_not_the_run_status() -> None:
    manager = RunStreamManager()
    store = _store(manager)
    store.create({}, run_id="r1")
    queue = manager.subscribe("r1")
    store.start("r1")  # a run.status item sits at the head of the queue
    for i in range(SUBSCRIBER_QUEUE_LIMIT + 50):
        manager.publish_tool("r1", call_id=str(i), tool="t", phase="p", status="started")
    items = [queue.get_nowait() for _ in range(queue.qsize())]
    assert queue.empty()
    assert len(items) <= SUBSCRIBER_QUEUE_LIMIT + 1
    assert not isinstance(items[0], ToolEvent)  # the status survived
    assert items[-1].call_id == str(SUBSCRIBER_QUEUE_LIMIT + 49)  # the newest survived


@pytest.mark.asyncio
async def test_stream_carries_tool_events_and_only_a_run_status_ends_it() -> None:
    manager = RunStreamManager()
    store = _store(manager)
    store.create({}, run_id="r1")
    store.start("r1")
    chunks: list[str] = []

    async def read() -> None:
        async for frame in run_event_stream("r1", store.get("r1"), manager):
            chunks.append(frame)

    task = asyncio.create_task(read())
    await asyncio.sleep(0)
    manager.publish_tool(
        "r1", call_id="c1", tool="calculix.run_fea", phase="simulation", status="started"
    )
    manager.publish_tool(
        "r1",
        call_id="c1",
        tool="calculix.run_fea",
        phase="simulation",
        status="finished",
        duration_ms=1200,
    )
    await asyncio.sleep(0.01)
    assert not task.done()  # progress did not end the stream
    manager.close("r1")
    await asyncio.wait_for(task, timeout=2)
    kinds = [event for event, _, _ in _frames(chunks)]
    assert kinds[0] == "run.status"
    assert kinds.count("tool.progress") == 2
    progress = [(d["status"], i) for e, d, i in _frames(chunks) if e == "tool.progress"]
    assert progress == [("started", "1"), ("finished", "2")]


@pytest.mark.asyncio
async def test_reconnect_with_last_event_id_replays_missed_events_once() -> None:
    manager = RunStreamManager()
    store = _store(manager)
    store.create({}, run_id="r1")
    store.start("r1")
    for i in range(4):
        manager.publish_tool("r1", call_id=str(i), tool="t", phase="p", status="started")
    chunks: list[str] = []

    async def read() -> None:
        async for frame in run_event_stream("r1", store.get("r1"), manager, last_event_id=2):
            chunks.append(frame)

    task = asyncio.create_task(read())
    await asyncio.sleep(0.01)
    manager.publish_tool("r1", call_id="live", tool="t", phase="p", status="started")
    await asyncio.sleep(0.01)
    manager.close("r1")
    await asyncio.wait_for(task, timeout=2)
    ids = [i for e, _, i in _frames(chunks) if e == "tool.progress"]
    assert ids == ["3", "4", "5"]


@pytest.mark.asyncio
async def test_scope_reports_started_then_finished_and_swallows_sink_errors() -> None:
    seen: list[dict[str, Any]] = []

    async def sink(event: dict[str, Any]) -> None:
        seen.append(event)
        if event["status"] == "finished":
            raise RuntimeError("gateway down")

    async with tool_event_scope("r1", "design", sink):
        report_tool_event("c1", "freecad.pad_sketch", "started")
        report_tool_event("c1", "freecad.pad_sketch", "finished", duration_ms=5)
    assert [e["status"] for e in seen] == ["started", "finished"]
    assert all(e["run_id"] == "r1" and e["phase"] == "design" for e in seen)
    assert all("arguments" not in e and "result" not in e for e in seen)


def test_reporting_outside_a_scope_is_a_no_op() -> None:
    report_tool_event("c1", "t", "started")


@pytest.mark.asyncio
async def test_scoped_brain_opens_a_scope_for_the_phase() -> None:
    seen: list[dict[str, Any]] = []

    async def sink(event: dict[str, Any]) -> None:
        seen.append(event)

    class Brain:
        async def run_phase(self, *, goal: str, phase: Any, context: Any) -> str:
            report_tool_event("c1", "twin.get_node", "started")
            return "done"

    class Phase:
        id = "design"

    out = await ScopedPhaseBrain(Brain(), "r9", sink).run_phase(
        goal="g", phase=Phase(), context=None
    )
    assert out == "done"
    assert seen == [
        {
            "run_id": "r9",
            "call_id": "c1",
            "tool": "twin.get_node",
            "phase": "design",
            "status": "started",
            "duration_ms": None,
        }
    ]


def test_ingest_route_publishes_and_404s_an_unknown_run() -> None:
    run_routes.reset_run_store()
    app = FastAPI()
    app.include_router(run_routes.router)
    client = TestClient(app)
    body = {"call_id": "c1", "tool": "calculix.run_fea", "phase": "simulation", "status": "started"}
    assert client.post("/v1/runs/nope/tool-events", json=body).status_code == 404

    run_routes._store.create({}, run_id="r1")
    queue = run_routes._stream_manager.subscribe("r1")
    resp = client.post("/v1/runs/r1/tool-events", json=body)
    assert resp.status_code == 202
    event = queue.get_nowait()
    assert isinstance(event, ToolEvent) and event.tool == "calculix.run_fea"

    bad = {**body, "status": "exploded"}
    assert client.post("/v1/runs/r1/tool-events", json=bad).status_code == 422


@pytest.mark.asyncio
async def test_runtime_call_tool_reports_started_and_finished_without_arguments() -> None:
    from orchestrator.harness.runtime import HarnessRuntime
    from orchestrator.harness.tools import ToolRegistry

    async def ok(args: dict[str, Any]) -> dict[str, Any]:
        return {"done": True}

    async def boom(args: dict[str, Any]) -> dict[str, Any]:
        raise ValueError("nope")

    tools = ToolRegistry()
    schema = {"type": "object", "properties": {"secret": {"type": "string"}}}
    tools.register_native("calc.ok", description="d", input_schema=schema, handler=ok)
    tools.register_native("calc.boom", description="d", input_schema=schema, handler=boom)
    runtime = HarnessRuntime.build(tools=tools)
    seen: list[dict[str, Any]] = []

    async def sink(event: dict[str, Any]) -> None:
        seen.append(event)

    async with tool_event_scope("r1", "simulation", sink):
        await runtime.call_tool("calc.ok", {"secret": "other-project-data"})
        with pytest.raises(ValueError):
            await runtime.call_tool("calc.boom", {"secret": "x"})
        await asyncio.sleep(0)
    assert [(e["tool"], e["status"]) for e in seen] == [
        ("calc.ok", "started"),
        ("calc.ok", "finished"),
        ("calc.boom", "started"),
        ("calc.boom", "failed"),
    ]
    assert "other-project-data" not in json.dumps(seen)
    assert seen[1]["duration_ms"] is not None
