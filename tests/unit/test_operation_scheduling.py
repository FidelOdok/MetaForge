"""Operation scheduling: supersede, stale results, coalescing, cancel and retry (FORGE-788).

CDE slice 2. Covers DI-003 (cancel, supersede, retry while prior valid
results stay), DEF-008 and PERF-004 (a late result never overwrites a newer
revision), RT-001 (a chat turn acknowledged as an operation), RT-028 (cancel
and retry offered only where supported) and RT-030 (a stale gate decision is
refused), as ``docs/operations.md`` states them.
"""

from __future__ import annotations

import asyncio
import base64
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_gateway.chat import routes as chat_routes
from api_gateway.chat.backend import InMemoryChatBackend
from api_gateway.chat.models import ChatMessageRecord
from api_gateway.operations import publisher as ops
from api_gateway.operations import sidecar
from api_gateway.operations.heads import item_head_revision, make_item_head_resolver
from api_gateway.operations.routes import publish_local
from api_gateway.operations.routes import router as operations_router
from api_gateway.operations.schemas import OperationEventIn
from api_gateway.operations.store import OperationStore
from api_gateway.runs import routes as run_routes
from api_gateway.runs.schemas import RunResponse
from api_gateway.twin.geometry_recorder import make_geometry_recorder
from orchestrator.design_flow.launcher import WorkflowNotFoundError
from tool_registry import jobs as jobs_module
from tool_registry.jobs import (
    JobNotRetryableError,
    JobRunner,
    JobStore,
    register_retry,
    set_default_job_runner,
    set_job_head_resolver,
)
from twin_core.api import InMemoryTwinAPI
from twin_core.items import ItemRevisionConflictError, find_item

PROJECT = "55555555-5555-5555-5555-555555555555"
TOOL = "twin.generate_concept_mesh"


def _step(body: str) -> str:
    return base64.b64encode(f"ISO-10303-21;\n{body}\nENDSEC;\n".encode()).decode("ascii")


@pytest.fixture(autouse=True)
def _blob_store(monkeypatch: pytest.MonkeyPatch) -> None:
    import digital_twin.storage.work_product_blobs as blobs

    monkeypatch.setattr(
        blobs,
        "store_work_product_blob",
        lambda node_id, filename, content, content_type="": f"wp/{node_id}/{filename}",
    )


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    yield
    set_job_head_resolver(None)
    ops.set_head_resolver(None)
    ops.set_job_cancel_relay(None)
    set_default_job_runner(None)
    jobs_module._retry_factories.pop(TOOL, None)  # noqa: SLF001


@pytest.fixture
def publisher() -> Iterator[ops.OperationPublisher]:
    pub = ops.init_operations(None)
    yield pub
    ops.init_operations(None)


@pytest.fixture
def client(publisher: ops.OperationPublisher) -> TestClient:
    app = FastAPI()
    app.include_router(operations_router)
    return TestClient(app)


class _Heads:
    """A head resolver over a dict, so a test can move a head."""

    def __init__(self, **heads: str) -> None:
        self.heads = dict(heads)

    async def __call__(self, project_id: str, target_key: str) -> str | None:
        return self.heads.get(target_key)


def _gated() -> tuple[asyncio.Event, Any]:
    gate = asyncio.Event()

    async def work() -> dict[str, Any]:
        await gate.wait()
        return {"node_id": "n-1"}

    return gate, work


def _types(pub: ops.OperationPublisher, op: str) -> list[str]:
    return [e.type.value for e in pub.store.events_after(PROJECT, 0) if e.operation_id == op]


# ── stale results never move the head (DI-003, DEF-008, PERF-004) ────────


async def test_a_stale_result_does_not_move_the_head() -> None:
    """The job's pinned write is refused by the twin; the job ends stale."""
    twin = InMemoryTwinAPI.create()
    record = make_geometry_recorder(twin, None)
    v1 = await record(step_base64=_step("a"), name="Shelf Bracket", project_id=PROJECT)
    key = v1["item_key"]
    runner = JobRunner(JobStore(":memory:"))
    gate = asyncio.Event()

    async def late_write() -> dict[str, Any]:
        await gate.wait()
        # The job works from @1 and pins its write to it, as a caller does.
        return dict(
            await record(
                step_base64=_step("late"),
                name="Shelf Bracket",
                project_id=PROJECT,
                item_key=f"{key}@1",
            )
        )

    job, _ = await runner.start(
        "cadquery.create_parametric",
        {"x": 1},
        late_write,
        session_id="s",
        project_id=PROJECT,
        target_key=key,
        base_revision="1",
    )
    await asyncio.sleep(0)
    # Meanwhile the person accepts a newer revision.
    v2 = await record(step_base64=_step("b"), name="Shelf Bracket", project_id=PROJECT)
    gate.set()
    done = await runner.wait(job.job_id, 2.0)

    assert done is not None and done.status == "stale"
    assert "not applied" in (done.error or "")
    item = await find_item(twin, key, PROJECT)
    assert item is not None and item.head_revision == 2
    assert str(item.head_node_id) == v2["node_id"]
    assert await item_head_revision(twin, PROJECT, key) == "2"


async def test_a_result_arriving_after_the_head_moved_is_kept_but_stale() -> None:
    heads = _Heads(**{"CAD-ARM": "1"})
    set_job_head_resolver(heads)
    sink: list[dict[str, Any]] = []

    async def publish(project_id: str, event: dict[str, Any]) -> None:
        sink.append(event)

    runner = JobRunner(JobStore(":memory:"), publisher=publish)
    gate, work = _gated()
    job, _ = await runner.start(
        TOOL,
        {"p": 1},
        work,
        session_id="s",
        project_id=PROJECT,
        target_key="CAD-ARM",
        base_revision="1",
    )
    await asyncio.sleep(0)
    heads.heads["CAD-ARM"] = "2"
    gate.set()
    done = await runner.wait(job.job_id, 2.0)
    await runner.flush_events()

    assert done is not None and done.status == "stale"
    assert done.result == {"node_id": "n-1"}  # kept on record, never adopted
    last = sink[-1]
    assert (last["type"], last["state"]) == ("stale", "stale")
    assert last["target_key"] == "CAD-ARM" and last["base_revision"] == "1"
    assert "revision 2" in last["message"]


async def test_a_head_that_moved_before_the_job_started_costs_nothing() -> None:
    set_job_head_resolver(_Heads(**{"CAD-ARM": "3"}))
    runner = JobRunner(JobStore(":memory:"))
    called: list[bool] = []

    async def work() -> dict[str, Any]:
        called.append(True)
        return {}

    job, _ = await runner.start(
        TOOL,
        {},
        work,
        session_id="s",
        project_id=PROJECT,
        target_key="CAD-ARM",
        base_revision="2",
    )
    done = await runner.wait(job.job_id, 2.0)
    assert done is not None and done.status == "stale"
    assert called == []


async def test_a_completed_event_on_a_moved_head_is_stored_stale(
    publisher: ops.OperationPublisher,
) -> None:
    ops.set_head_resolver(_Heads(**{"CAD-ARM": "5"}))
    event = OperationEventIn(
        operation_id="job:j1",
        type="completed",
        state="completed",
        target_key="CAD-ARM",
        base_revision="4",
        action_refs=[{"kind": "open", "id": "/x", "label": "Open"}],
    )
    result = await publisher.ingest_screened(PROJECT, event)
    assert result.event is not None
    assert (result.event.type.value, result.event.state.value) == ("stale", "stale")
    assert result.event.target_revision == "5" and result.event.action_refs == []
    # The same check on the in-process seam; a head that has not moved passes.
    await publish_local(
        PROJECT,
        {
            "operation_id": "job:j2",
            "type": "completed",
            "state": "completed",
            "target_key": "CAD-ARM",
            "base_revision": "5",
        },
    )
    snap = publisher.store.get(PROJECT, "job:j2")
    assert snap is not None and snap.state == "completed"


# ── supersede (DI-003, DEF-008) ─────────────────────────────────────────


async def test_a_new_job_on_the_same_target_supersedes_the_older_one() -> None:
    runner = JobRunner(JobStore(":memory:"))
    _gate_a, work_a = _gated()
    _gate_b, work_b = _gated()
    _gate_c, work_c = _gated()
    older, _ = await runner.start(
        TOOL, {"v": 1}, work_a, session_id="s", project_id=PROJECT, target_key="CAD-ARM"
    )
    other, _ = await runner.start(
        TOOL, {"v": 1}, work_c, session_id="s", project_id=PROJECT, target_key="CAD-LEG"
    )
    newer, _ = await runner.start(
        TOOL, {"v": 2}, work_b, session_id="s", project_id=PROJECT, target_key="CAD-ARM"
    )
    assert runner.get(older.job_id).status == "superseded"  # type: ignore[union-attr]
    assert newer.job_id in runner._tasks  # noqa: SLF001
    assert runner.get(other.job_id).status in {"queued", "running"}  # type: ignore[union-attr]
    for job_id in (newer.job_id, other.job_id):
        runner.cancel(job_id)


def test_the_publisher_supersedes_older_open_jobs_and_refuses_their_late_result(
    publisher: ops.OperationPublisher,
) -> None:
    publisher.publish(PROJECT, "job:a", "started", "running", sequence=20, target_key="CAD-ARM")
    publisher.publish(PROJECT, "run:r", "started", "running", target_key="CAD-ARM")
    publisher.publish(PROJECT, "job:b", "queued", "queued", sequence=10, target_key="CAD-ARM")
    a = publisher.store.get(PROJECT, "job:a")
    assert a is not None and a.state == "superseded"
    assert publisher.store.get(PROJECT, "run:r").state == "running"  # type: ignore[union-attr]
    # The superseded job's runner reports its own ending later: a duplicate.
    again = publisher.publish(
        PROJECT,
        "job:a",
        "superseded",
        "superseded",
        sequence=90,
        event_id=ops.terminal_event_id("job:a", "superseded"),
    )
    assert again is not None and _types(publisher, "job:a").count("superseded") == 1
    # Its late result is refused: it can never be reported as the current one.
    assert publisher.publish(PROJECT, "job:a", "completed", "completed", sequence=91) is None
    assert publisher.store.get(PROJECT, "job:b").state == "queued"  # type: ignore[union-attr]


# ── coalescing (DEF-008, PERF-003) ──────────────────────────────────────


async def test_an_identical_call_from_another_session_joins_the_open_job() -> None:
    runner = JobRunner(JobStore(":memory:"))
    _gate, work = _gated()
    first, reused = await runner.start(
        TOOL, {"p": "arm"}, work, session_id="s1", project_id=PROJECT, target_key="CAD-ARM"
    )
    assert reused is False
    again, reused = await runner.start(
        TOOL, {"p": "arm"}, work, session_id="s2", project_id=PROJECT, target_key="CAD-ARM"
    )
    assert reused is True and again.job_id == first.job_id
    # Different arguments are a new request, which supersedes the first.
    third, reused = await runner.start(
        TOOL, {"p": "leg"}, work, session_id="s2", project_id=PROJECT, target_key="CAD-ARM"
    )
    assert reused is False and third.job_id != first.job_id
    runner.cancel(third.job_id)


async def test_coalescing_stops_at_the_window(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [1000.0]
    runner = JobRunner(JobStore(":memory:", clock=lambda: now[0]), clock=lambda: now[0])

    async def work() -> dict[str, Any]:
        return {"ok": True}

    first, _ = await runner.start(TOOL, {"p": 1}, work, session_id="s1", project_id=PROJECT)
    await runner.wait(first.job_id, 2.0)
    now[0] += jobs_module.COALESCE_WINDOW_S + 1
    later, reused = await runner.start(TOOL, {"p": 1}, work, session_id="s2", project_id=PROJECT)
    assert reused is False and later.job_id != first.job_id
    monkeypatch.setenv(jobs_module.COALESCE_WINDOW_ENV, "0")
    off, reused = await runner.start(TOOL, {"p": 1}, work, session_id="s3", project_id=PROJECT)
    assert reused is False and off.job_id != later.job_id


# ── retry (DI-003, RT-024, RT-028) ──────────────────────────────────────


async def test_retry_runs_a_failed_job_again_and_names_the_original() -> None:
    sink: list[dict[str, Any]] = []

    async def publish(project_id: str, event: dict[str, Any]) -> None:
        sink.append(event)

    runner = JobRunner(JobStore(":memory:"), publisher=publish)

    async def boom() -> dict[str, Any]:
        raise RuntimeError("provider down")

    failed, _ = await runner.start(TOOL, {"p": 1}, boom, session_id="s", project_id=PROJECT)
    await runner.wait(failed.job_id, 2.0)
    await runner.flush_events()
    # No retry factory: the failure offers no retry.
    assert "action_refs" not in sink[-1]
    with pytest.raises(JobNotRetryableError):
        await runner.retry(failed.job_id)

    seen: list[dict[str, Any]] = []

    def factory(arguments: dict[str, Any]) -> Any:
        seen.append(arguments)

        async def work() -> dict[str, Any]:
            return {"node_id": "n-2"}

        return work

    register_retry(TOOL, factory)
    failed2, _ = await runner.start(TOOL, {"p": 2}, boom, session_id="s", project_id=PROJECT)
    await runner.wait(failed2.job_id, 2.0)
    await runner.flush_events()
    assert sink[-1]["action_refs"] == [
        {"kind": "retry", "id": f"job:{failed2.job_id}", "label": "Retry"}
    ]
    new = await runner.retry(failed2.job_id)
    assert new.job_id != failed2.job_id and new.retry_of == failed2.job_id
    assert seen == [{"p": 2}]
    done = await runner.wait(new.job_id, 2.0)
    await runner.flush_events()
    assert done is not None and done.status == "succeeded"
    assert sink[-1]["retry_of"] == f"job:{failed2.job_id}"
    with pytest.raises(JobNotRetryableError):
        await runner.retry(new.job_id)  # it did not fail


def test_an_older_job_store_gains_the_new_columns(tmp_path: Path) -> None:
    path = tmp_path / "jobs.db"
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE tool_jobs (job_id TEXT PRIMARY KEY, tool_id TEXT NOT NULL,"
        " arguments_hash TEXT NOT NULL, session_id TEXT NOT NULL, project_id TEXT,"
        " status TEXT NOT NULL, created_at REAL NOT NULL, started_at REAL, finished_at REAL,"
        " result TEXT, error TEXT, expected_seconds REAL, notified INTEGER NOT NULL DEFAULT 0)"
    )
    db.execute(
        "INSERT INTO tool_jobs VALUES ('j1', 't', 'h', 's', 'p', 'succeeded', 1, 1, 2,"
        " NULL, NULL, NULL, 0)"
    )
    db.commit()
    db.close()
    store = JobStore(path)
    job = store.get("j1")
    assert job is not None and job.status == "succeeded" and job.arguments is None
    assert job.target_key is None and job.retry_of is None
    store.close()


def test_an_older_operation_store_gains_target_key(tmp_path: Path) -> None:
    path = tmp_path / "ops.db"
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE operations (project_id TEXT NOT NULL, operation_id TEXT NOT NULL,"
        " kind TEXT NOT NULL, state TEXT NOT NULL, terminal INTEGER NOT NULL,"
        " last_sequence INTEGER NOT NULL, updated_at REAL NOT NULL, body TEXT NOT NULL,"
        " PRIMARY KEY (project_id, operation_id))"
    )
    db.commit()
    db.close()
    store = OperationStore(path)
    store.append(
        PROJECT,
        OperationEventIn(operation_id="job:x", type="queued", state="queued", target_key="K"),
    )
    assert [s.operation_id for s in store.active_for_target(PROJECT, "K")] == ["job:x"]
    store.close()


# ── routes: cancel and retry (RT-028) ───────────────────────────────────


def test_cancel_and_retry_routes_refuse_what_they_cannot_do(client: TestClient) -> None:
    run_routes.reset_run_store()
    base = f"/v1/projects/{PROJECT}/operations"
    assert client.post(f"{base}/job:missing/cancel").status_code == 404
    assert client.post(f"{base}/job:missing/retry").status_code == 404

    ops.publish(PROJECT, "chat:t1", "started", "running")
    assert client.post(f"{base}/chat:t1/retry").status_code == 409  # not failed
    ops.publish(PROJECT, "chat:t1", "failed", "failed")
    assert client.post(f"{base}/chat:t1/retry").status_code == 409  # a chat turn

    plain = run_routes.get_run_store().create({"project_id": PROJECT})
    run_routes.get_run_store().start(plain.id)
    run_routes.get_run_store().fail(plain.id, "x")
    resp = client.post(f"{base}/run:{plain.id}/retry")
    assert resp.status_code == 409 and "design-flow" in resp.json()["detail"]
    events = [
        e
        for e in ops.get_operation_publisher().store.events_after(PROJECT, 0)
        if e.operation_id == f"run:{plain.id}"
    ]
    assert events[-1].action_refs == []  # a plain run offers no retry

    # A job this gateway does not run and no sidecar configured: 409.
    ops.publish(PROJECT, "job:elsewhere", "started", "running")
    assert client.post(f"{base}/job:elsewhere/cancel").status_code == 409
    run_routes.reset_run_store()


async def test_cancel_relays_to_the_sidecar(
    publisher: ops.OperationPublisher, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, str]] = []
    answer = {"status": 202, "body": {"job_id": "j9", "status": "cancelled"}}

    async def relay(job_id: str, action: str, **_: Any) -> sidecar.RelayResult:
        calls.append((job_id, action))
        return sidecar.RelayResult(int(answer["status"]), dict(answer["body"]))  # type: ignore[arg-type]

    monkeypatch.setattr(sidecar, "relay_job_action", relay)
    publisher.publish(PROJECT, "job:j9", "started", "running", sequence=20)
    app = FastAPI()
    app.include_router(operations_router)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as http:
        resp = await http.post(f"/v1/projects/{PROJECT}/operations/job:j9/cancel")
        assert resp.status_code == 202, resp.text
        assert calls == [("j9", "cancel")]
        snap = publisher.store.get(PROJECT, "job:j9")
        assert snap is not None and snap.state == "cancelled"

        publisher.publish(PROJECT, "job:j10", "started", "running", sequence=20)
        answer["status"], answer["body"] = 0, {"detail": "unreachable"}
        assert (
            await http.post(f"/v1/projects/{PROJECT}/operations/job:j10/cancel")
        ).status_code == 503
        answer["status"], answer["body"] = 404, {"detail": "unknown"}
        assert (
            await http.post(f"/v1/projects/{PROJECT}/operations/job:j10/cancel")
        ).status_code == 409

        publisher.publish(PROJECT, "job:j11", "failed", "failed", sequence=90)
        answer["status"], answer["body"] = 202, {"job_id": "j12", "status": "queued"}
        resp = await http.post(f"/v1/projects/{PROJECT}/operations/job:j11/retry")
        assert resp.status_code == 202, resp.text
        assert resp.json()["operation_id"] == "job:j12"
        assert resp.json()["retry_of"] == "job:j11"


async def test_retry_route_reruns_a_failed_job_this_gateway_ran(
    publisher: ops.OperationPublisher,
) -> None:
    runner = JobRunner(JobStore(":memory:"), publisher=publish_local)
    set_default_job_runner(runner)

    async def boom() -> dict[str, Any]:
        raise RuntimeError("down")

    def factory(arguments: dict[str, Any]) -> Any:
        async def work() -> dict[str, Any]:
            return {}

        return work

    register_retry(TOOL, factory)
    failed, _ = await runner.start(TOOL, {"p": 1}, boom, session_id="s", project_id=PROJECT)
    await runner.wait(failed.job_id, 2.0)
    await runner.flush_events()
    snap = publisher.store.get(PROJECT, f"job:{failed.job_id}")
    assert snap is not None and [a.kind for a in snap.action_refs] == ["retry"]

    app = FastAPI()
    app.include_router(operations_router)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as http:
        resp = await http.post(f"/v1/projects/{PROJECT}/operations/job:{failed.job_id}/retry")
        assert resp.status_code == 202, resp.text
        new_op = resp.json()["operation_id"]
        await runner.flush_events()
        new = publisher.store.get(PROJECT, new_op)
        assert new is not None and new.retry_of == f"job:{failed.job_id}"
        # The original stays failed and is not retried twice by a stale button.
        again = await http.post(f"/v1/projects/{PROJECT}/operations/{new_op}/retry")
        assert again.status_code == 409


class _Handle:
    def __init__(self, error: Exception | None = None) -> None:
        self.cancelled = False
        self.error = error

    async def cancel(self) -> None:
        if self.error is not None:
            raise self.error
        self.cancelled = True


class _Client:
    def __init__(self, handle: _Handle) -> None:
        self.handle = handle

    def get_workflow_handle(self, _workflow_id: str) -> _Handle:
        return self.handle


def test_cancel_stops_a_temporal_design_flow_and_keeps_committed_work(
    client: TestClient,
) -> None:
    from orchestrator.design_flow.launcher import DesignFlowLauncher

    run_routes.reset_run_store()
    handle = _Handle()
    run_routes.set_flow_launcher(DesignFlowLauncher(client=_Client(handle)))
    store = run_routes.get_run_store()
    try:
        run = store.create({"project_id": PROJECT, "flow": "design_v1", "flow_engine": "temporal"})
        store.start(run.id)
        snap = ops.get_operation_publisher().store.get(PROJECT, f"run:{run.id}")
        assert snap is not None and [a.kind for a in snap.action_refs] == ["cancel"]
        resp = client.post(f"/v1/projects/{PROJECT}/operations/run:{run.id}/cancel")
        assert resp.status_code == 202, resp.text
        assert handle.cancelled
        assert store.get(run.id).status.value == "canceled"

        # A workflow already gone still lets the record be cancelled.
        gone = store.create({"project_id": PROJECT, "flow": "design_v1", "flow_engine": "temporal"})
        store.start(gone.id)
        handle.error = WorkflowNotFoundError(gone.id, "not found")
        assert (
            client.post(f"/v1/projects/{PROJECT}/operations/run:{gone.id}/cancel").status_code
            == 202
        )

        # An engine that cannot be reached: 503, and the run keeps running.
        down = store.create({"project_id": PROJECT, "flow": "design_v1", "flow_engine": "temporal"})
        store.start(down.id)
        handle.error = RuntimeError("connection refused")
        assert (
            client.post(f"/v1/projects/{PROJECT}/operations/run:{down.id}/cancel").status_code
            == 503
        )
        assert store.get(down.id).status.value == "running"
    finally:
        run_routes.set_flow_launcher(None)
        run_routes.reset_run_store()


def test_retry_of_a_failed_design_flow_starts_a_new_run_naming_it(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from api_gateway.runs.schemas import CreateRunRequest

    run_routes.reset_run_store()
    store = run_routes.get_run_store()
    started: list[dict[str, Any]] = []

    async def create_run(body: CreateRunRequest) -> RunResponse:
        started.append(dict(body.request))
        new = store.create(body.request)
        store.start(new.id)
        return RunResponse.from_run(store.get(new.id))

    monkeypatch.setattr(run_routes, "create_run", create_run)
    try:
        run = store.create(
            {"project_id": PROJECT, "flow": "design_v1", "goal": "a bracket", "flow_engine": "x"}
        )
        store.start(run.id)
        store.fail(run.id, "phase failed")
        snap = ops.get_operation_publisher().store.get(PROJECT, f"run:{run.id}")
        assert snap is not None and [a.kind for a in snap.action_refs] == ["retry"]
        resp = client.post(f"/v1/projects/{PROJECT}/operations/run:{run.id}/retry")
        assert resp.status_code == 202, resp.text
        assert started[0]["goal"] == "a bracket" and started[0]["retry_of"] == run.id
        assert "flow_engine" not in started[0]
        new_op = resp.json()["operation_id"]
        events = [
            e
            for e in ops.get_operation_publisher().store.events_after(PROJECT, 0)
            if e.operation_id == new_op
        ]
        assert events[0].type.value == "accepted"
        assert events[0].retry_of == f"run:{run.id}"
    finally:
        run_routes.reset_run_store()


# ── the sidecar's side of the relay ─────────────────────────────────────


async def test_the_sidecar_cancels_and_retries_its_own_jobs() -> None:
    from metaforge.mcp.__main__ import build_http_app
    from metaforge.mcp.server import UnifiedMcpServer

    runner = JobRunner(JobStore(":memory:"))
    set_default_job_runner(runner)
    app = build_http_app(UnifiedMcpServer([]), enable_sse=False, api_key="secret")
    _gate, work = _gated()
    job, _ = await runner.start(TOOL, {}, work, session_id="s", project_id=PROJECT)
    transport = httpx.ASGITransport(app=app)
    auth = {"Authorization": "Bearer secret"}
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as http:
        assert (await http.post(f"/v1/jobs/{job.job_id}/cancel")).status_code == 401
        assert (await http.post("/v1/jobs/nope/cancel", headers=auth)).status_code == 404
        resp = await http.post(f"/v1/jobs/{job.job_id}/cancel", headers=auth)
        assert resp.status_code == 202 and resp.json()["status"] == "cancelled"
        assert (await http.post(f"/v1/jobs/{job.job_id}/cancel", headers=auth)).status_code == 409
        assert (await http.post(f"/v1/jobs/{job.job_id}/retry", headers=auth)).status_code == 409


async def test_the_relay_posts_with_the_client_key(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(202, json={"job_id": "j1", "status": "cancelled"})

    monkeypatch.setenv("METAFORGE_MCP_URL", "http://mcp-http:8765/mcp")
    monkeypatch.setenv("METAFORGE_MCP_CLIENT_KEY", "k")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await sidecar.relay_job_action("j1", "cancel", client=http)
    assert result is not None and result.status == 202
    assert str(seen[0].url) == "http://mcp-http:8765/v1/jobs/j1/cancel"
    assert seen[0].headers["authorization"] == "Bearer k"
    monkeypatch.delenv("METAFORGE_MCP_URL")
    assert await sidecar.relay_job_action("j1", "cancel") is None


# ── chat turns as operations (RT-001) ───────────────────────────────────


@pytest.fixture
def chat_client(
    publisher: ops.OperationPublisher, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, list[ChatMessageRecord | None]]]:
    chat_routes.init_chat_backend(InMemoryChatBackend.create())
    replies: list[ChatMessageRecord | None] = []

    async def fake_agent(thread: Any, content: str, **_: Any) -> ChatMessageRecord | None:
        return replies.pop(0) if replies else None

    monkeypatch.setattr(chat_routes, "_invoke_agent", fake_agent)
    app = FastAPI()
    app.include_router(chat_routes.router)
    yield TestClient(app), replies


def _reply(thread_id: str, *, error: bool = False) -> ChatMessageRecord:
    return ChatMessageRecord(
        id="r1",
        thread_id=thread_id,
        actor_id="agent",
        actor_kind="system" if error else "agent",
        content="secret reply text",
        status="error" if error else "sent",
    )


def test_a_chat_turn_is_an_operation_from_accepted_to_completed(
    chat_client: tuple[TestClient, list[ChatMessageRecord | None]],
    publisher: ops.OperationPublisher,
) -> None:
    client, replies = chat_client
    thread = client.post(
        "/v1/chat/threads", json={"scope_kind": "project", "scope_entity_id": PROJECT}
    ).json()
    replies.append(_reply(thread["id"]))
    resp = client.post(
        f"/v1/chat/threads/{thread['id']}/messages",
        json={
            "content": "make the PROMPT wider",
            "actor_id": "u",
            "actor_kind": "user",
            "client_turn_id": "ins-abc-1",
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["operation_id"] == "chat:ins-abc-1"
    events = [e for e in publisher.store.events_after(PROJECT, 0)]
    assert [(e.type.value, e.state.value) for e in events] == [
        ("accepted", "queued"),
        ("started", "running"),
        ("completed", "completed"),
    ]
    assert all(e.operation_id == "chat:ins-abc-1" and e.kind.value == "chat" for e in events)
    assert all("PROMPT" not in e.message and "secret" not in e.message for e in events)
    assert events[0].action_refs == []  # a chat turn is neither cancellable nor retryable

    # The same client id again cannot reuse an operation: the message id is used.
    replies.append(_reply(thread["id"], error=True))
    second = client.post(
        f"/v1/chat/threads/{thread['id']}/messages",
        json={
            "content": "again",
            "actor_id": "u",
            "actor_kind": "user",
            "client_turn_id": "ins-abc-1",
        },
    ).json()
    assert second["operation_id"] == f"chat:{second['id']}"
    snap = publisher.store.get(PROJECT, second["operation_id"])
    assert snap is not None and snap.state == "failed" and snap.severity == "error"


def test_a_turn_outside_a_project_or_from_an_agent_reports_nothing(
    chat_client: tuple[TestClient, list[ChatMessageRecord | None]],
    publisher: ops.OperationPublisher,
) -> None:
    client, _ = chat_client
    thread = client.post(
        "/v1/chat/threads", json={"scope_kind": "session", "scope_entity_id": "s"}
    ).json()
    body = client.post(
        f"/v1/chat/threads/{thread['id']}/messages",
        json={"content": "hi", "actor_id": "u", "actor_kind": "user"},
    ).json()
    assert body["operation_id"] is None
    assert publisher.store.cursor(PROJECT) == 0


# ── the gateway's head resolver ─────────────────────────────────────────


async def test_the_item_head_resolver_reads_the_twin() -> None:
    twin = InMemoryTwinAPI.create()
    record = make_geometry_recorder(twin, None)
    v1 = await record(step_base64=_step("a"), name="Arm", project_id=PROJECT)
    resolve = make_item_head_resolver(lambda: twin)
    assert await resolve(PROJECT, v1["item_key"]) == "1"
    assert await resolve(PROJECT, f"{v1['item_key']}@1") == "1"
    assert await resolve(PROJECT, "CAD-NOPE") is None
    assert await resolve("not-a-uuid", v1["item_key"]) is None
    assert await resolve(PROJECT, "has space") is None
    with pytest.raises(ItemRevisionConflictError):
        await record(
            step_base64=_step("b"), name="Arm", project_id=PROJECT, item_key=f"{v1['item_key']}@9"
        )
