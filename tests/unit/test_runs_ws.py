"""Unit tests for run WebSocket streaming (MET-547, Phase 4)."""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_gateway.runs.routes import get_run_store, reset_run_store, router
from api_gateway.runs.streaming import (
    RunStreamManager,
    run_event_payload,
    run_ws_loop,
)
from orchestrator.harness.runs import ApprovalDecision, InMemoryRunStore


def _store(manager: RunStreamManager) -> InMemoryRunStore:
    ticks = iter(range(1, 10_000))
    return InMemoryRunStore(clock=lambda: float(next(ticks)), on_transition=manager.publish)


def test_payload_shape() -> None:
    manager = RunStreamManager()
    store = _store(manager)
    run = store.create({}, run_id="r1")
    payload = run_event_payload(run)
    assert payload == {
        "id": "r1",
        "status": "queued",
        "updated_at": run.updated_at,
        "approval_reason": None,
        "error": None,
    }


@pytest.mark.asyncio
async def test_ws_loop_snapshot_then_terminal() -> None:
    manager = RunStreamManager()
    store = _store(manager)
    store.create({}, run_id="r1")
    store.start("r1")
    snapshot = store.get("r1")

    sent: list[dict] = []

    async def send_json(payload: dict) -> None:
        sent.append(payload)

    task = asyncio.create_task(run_ws_loop(send_json, "r1", snapshot, manager))
    await asyncio.sleep(0)
    store.complete("r1", result={"ok": True})
    await asyncio.wait_for(task, timeout=2.0)

    assert [p["status"] for p in sent] == ["running", "completed"]


@pytest.mark.asyncio
async def test_ws_loop_ends_immediately_if_terminal() -> None:
    manager = RunStreamManager()
    store = _store(manager)
    store.create({}, run_id="r1")
    store.start("r1")
    store.request_approval("r1")
    store.submit_approval("r1", ApprovalDecision.REJECT)
    sent: list[dict] = []

    async def send_json(payload: dict) -> None:
        sent.append(payload)

    await run_ws_loop(send_json, "r1", store.get("r1"), manager)
    assert [p["status"] for p in sent] == ["rejected"]


def test_ws_endpoint_receives_snapshot() -> None:
    reset_run_store()
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    run_id = client.post("/v1/runs", json={"start": False}).json()["id"]
    # queued is non-terminal, so drive it terminal for a deterministic single frame.
    store = get_run_store()
    store.start(run_id)
    store.request_approval(run_id)
    store.submit_approval(run_id, ApprovalDecision.REJECT)

    with client.websocket_connect(f"/v1/runs/{run_id}/ws") as ws:
        frame = ws.receive_json()
    assert frame["id"] == run_id
    assert frame["status"] == "rejected"
