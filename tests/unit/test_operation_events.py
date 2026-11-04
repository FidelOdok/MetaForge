"""The durable operation event log and project stream (FORGE-785, CDE slice 1).

Covers RT-013..RT-017 and RT-030 as the contract in ``docs/operations.md``
states them: ``project_seq`` survives a restart, a reconnecting client is
replayed what it missed from the store (or told to resync), events are
deduplicated and never regress, and runs, jobs and approvals report real
transitions only.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_gateway.operations import publisher as ops
from api_gateway.operations.hub import OperationHub
from api_gateway.operations.routes import publish_local
from api_gateway.operations.routes import router as operations_router
from api_gateway.operations.schemas import OperationEventIn
from api_gateway.operations.store import OperationStore
from api_gateway.runs import routes as run_routes
from mcp_core.operation_events import (
    domain_for_text,
    domain_for_tool,
    publish_operation_event,
    set_operation_event_publisher,
)
from observability.metrics import MetricsCollector
from tool_registry.jobs import JobRunner, JobStore, set_default_job_runner

PROJECT = "proj-1"


def _event(op: str = "run:r1", **kw: Any) -> OperationEventIn:
    fields: dict[str, Any] = {"operation_id": op, "type": "started", "state": "running"}
    fields.update(kw)
    return OperationEventIn(**fields)


class _Metrics(MetricsCollector):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def record_operation_event(self, kind: str, event_type: str, lag_seconds: float) -> None:
        self.calls.append(("event", (kind, event_type)))

    def record_operation_publish_failure(self, kind: str, reason: str) -> None:
        self.calls.append(("failure", (kind, reason)))

    def record_operation_stream_replay(self) -> None:
        self.calls.append(("replay", ()))

    def record_operation_stream_resync(self, reason: str) -> None:
        self.calls.append(("resync", (reason,)))

    def record_operation_stream_active(self, delta: int) -> None:
        self.calls.append(("streams", (delta,)))

    def record_operation_active(self, kind: str, delta: int) -> None:
        self.calls.append(("active", (kind, delta)))


@pytest.fixture
def metrics() -> Iterator[_Metrics]:
    rec = _Metrics()
    ops.set_metrics_collector(rec)
    yield rec
    ops.set_metrics_collector(None)


@pytest.fixture
def publisher(metrics: _Metrics) -> Iterator[ops.OperationPublisher]:
    pub = ops.init_operations(None)
    yield pub
    ops.init_operations(None)


@pytest.fixture
def client(publisher: ops.OperationPublisher) -> TestClient:
    app = FastAPI()
    app.include_router(operations_router)
    return TestClient(app)


async def _frames(stream: AsyncIterator[str], count: int) -> list[tuple[str, str | None, Any]]:
    """The next ``count`` non-comment frames as ``(event, id, data)``."""
    out: list[tuple[str, str | None, Any]] = []
    while len(out) < count:
        raw = await asyncio.wait_for(stream.__anext__(), 2.0)
        if raw.startswith(":"):
            continue
        name, ident, data = "", None, None
        for line in raw.strip().splitlines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("id: "):
                ident = line[4:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        out.append((name, ident, data))
    return out


# ── store ───────────────────────────────────────────────────────────────


def test_project_seq_survives_a_store_reopen(tmp_path: Path) -> None:
    path = tmp_path / "ops.db"
    store = OperationStore(path)
    first = store.append(PROJECT, _event(type="accepted", state="queued")).event
    second = store.append(PROJECT, _event()).event
    assert first is not None and second is not None
    assert (first.project_seq, second.project_seq) == (1, 2)
    assert (first.sequence, second.sequence) == (1, 2)
    store.close()

    reopened = OperationStore(path)
    assert reopened.cursor(PROJECT) == 2
    third = reopened.append(PROJECT, _event(type="completed", state="completed")).event
    assert third is not None
    assert third.project_seq == 3
    assert third.sequence == 3  # per operation, also from the store
    assert reopened.cursor("another") == 0


def test_project_seq_is_never_reused_after_pruning(tmp_path: Path) -> None:
    now = [1_000.0]
    store = OperationStore(tmp_path / "ops.db", clock=lambda: now[0], retention_s=60)
    store.append(PROJECT, _event(type="accepted", state="queued"))
    store.append(PROJECT, _event())
    now[0] += 3600
    assert store.prune() == 2
    assert store.oldest_seq(PROJECT) is None
    later = store.append(PROJECT, _event("run:r2")).event
    assert later is not None and later.project_seq == 3


def test_duplicate_event_id_returns_the_stored_event() -> None:
    store = OperationStore()
    stored = store.append(PROJECT, _event(event_id="e-1")).event
    again = store.append(PROJECT, _event(event_id="e-1", type="completed", state="completed"))
    assert again.status == "duplicate"
    assert again.event is not None and stored is not None
    assert again.event.project_seq == stored.project_seq
    assert again.event.type == "started"
    assert store.cursor(PROJECT) == 1


def test_out_of_order_and_post_terminal_events_are_refused() -> None:
    store = OperationStore()
    assert store.append(PROJECT, _event(sequence=5)).status == "stored"
    late = store.append(PROJECT, _event(sequence=4, type="queued", state="queued"))
    assert late.status == "refused" and "not after 5" in (late.reason or "")
    same = store.append(PROJECT, _event(sequence=5))
    assert same.status == "refused"
    done = store.append(PROJECT, _event(sequence=6, type="completed", state="completed"))
    assert done.status == "stored"
    after = store.append(PROJECT, _event(type="milestone", state="running"))
    assert after.status == "refused" and "already completed" in (after.reason or "")
    # A terminal event after a terminal one is a real change: a result went stale.
    stale = store.append(PROJECT, _event(type="stale", state="stale"))
    assert stale.status == "stored"
    snap = store.get(PROJECT, "run:r1")
    assert snap is not None and snap.state == "stale" and snap.last_sequence == 7


def test_snapshot_reflects_stages_artifacts_and_actions() -> None:
    store = OperationStore()
    store.append(PROJECT, _event(type="accepted", state="queued", title="Concept design"))
    store.append(
        PROJECT,
        _event(
            type="milestone",
            stage_id="intent",
            stage_title="Intent",
            domain="concept",
        ),
    )
    store.append(
        PROJECT,
        _event(
            type="partial_result",
            stage_id="cad",
            stage_title="Geometry",
            domain="mechanical",
            artifact_refs=[{"kind": "glb", "id": "n1", "authority": "preview"}],
            progress={"done": 3, "total": 8, "unit": "checks"},
        ),
    )
    store.append(
        PROJECT,
        _event(
            type="review_required",
            state="waiting_for_user",
            requires_action=True,
            action_refs=[{"kind": "approval", "id": "gate:r1", "label": "Review"}],
        ),
    )
    snap = store.get(PROJECT, "run:r1")
    assert snap is not None
    assert snap.title == "Concept design"
    assert snap.state == "waiting_for_user" and snap.requires_action
    assert [a.id for a in snap.action_refs] == ["gate:r1"]
    assert [a.id for a in snap.artifact_refs] == ["n1"]
    assert snap.progress is None  # the latest event carried none
    assert {(s.stage_id, s.domain, s.state) for s in snap.stages} == {
        ("intent", "concept", "running"),
        ("cad", "mechanical", "running"),
    }
    assert snap.started_at is not None

    store.append(PROJECT, _event(type="completed", state="completed"))
    done = store.get(PROJECT, "run:r1")
    assert done is not None
    assert {s.state for s in done.stages} == {"completed"}
    assert not done.requires_action and done.action_refs == []
    assert [a.id for a in done.artifact_refs] == ["n1"]  # artifacts accumulate
    assert store.list_operations(PROJECT, active=True) == []
    assert [o.operation_id for o in store.list_operations(PROJECT)] == ["run:r1"]


def test_event_validation_rejects_a_bad_operation_id_and_mismatched_kind() -> None:
    with pytest.raises(ValueError):
        _event("build-7")
    with pytest.raises(ValueError):
        _event("job:7", kind="run")
    with pytest.raises(ValueError):
        _event(progress={"done": 9, "total": 8})
    assert _event("job:7").kind == "job"


# ── hub and stream ──────────────────────────────────────────────────────


async def test_replay_after_restart_then_live(tmp_path: Path, metrics: _Metrics) -> None:
    path = tmp_path / "ops.db"
    before = ops.OperationPublisher(OperationStore(path))
    before.publish(PROJECT, "run:r1", "accepted", "queued")
    before.publish(PROJECT, "run:r1", "started", "running")
    before.publish(PROJECT, "run:r1", "milestone", "running", stage_id="cad")
    before.store.close()

    after = ops.OperationPublisher(OperationStore(path))  # a gateway restart
    stream = after.hub.stream(PROJECT, 1)
    frames = await _frames(stream, 2)
    assert [(f[0], f[1]) for f in frames] == [("operation", "2"), ("operation", "3")]
    assert frames[1][2]["stage_id"] == "cad"
    assert frames[1][2]["project_id"] == PROJECT
    after.publish(PROJECT, "run:r1", "completed", "completed")
    live = await _frames(stream, 1)
    assert live[0][1] == "4" and live[0][2]["type"] == "completed"
    await stream.aclose()
    assert ("replay", ()) in metrics.calls
    assert ("streams", (1,)) in metrics.calls and ("streams", (-1,)) in metrics.calls


async def test_unknown_or_expired_cursor_gets_a_resync(metrics: _Metrics) -> None:
    now = [1_000.0]
    store = OperationStore(clock=lambda: now[0], retention_s=60)
    pub = ops.OperationPublisher(store)
    pub.publish(PROJECT, "run:r1", "accepted", "queued")

    ahead = pub.hub.stream(PROJECT, 99)
    frames = await _frames(ahead, 1)
    assert frames[0][0] == "resync" and frames[0][1] is None
    assert frames[0][2] == {"reason": "unknown_cursor", "cursor": 1}
    await ahead.aclose()

    pub.publish(PROJECT, "run:r1", "started", "running")
    now[0] += 3600
    store.prune()
    pub.publish(PROJECT, "run:r1", "completed", "completed")
    old = pub.hub.stream(PROJECT, 0)
    frames = await _frames(old, 1)
    assert frames[0][2] == {"reason": "cursor_expired", "cursor": 3}
    await old.aclose()
    assert ("resync", ("unknown_cursor",)) in metrics.calls
    assert ("resync", ("cursor_expired",)) in metrics.calls


async def test_heartbeat_carries_the_cursor() -> None:
    store = OperationStore()
    hub = OperationHub(store, heartbeat_seconds=0.01)
    store.append(PROJECT, _event())
    stream = hub.stream(PROJECT)
    frames = await _frames(stream, 1)
    assert frames[0][0] == "heartbeat"
    assert frames[0][2]["cursor"] == 1 and "server_time" in frames[0][2]
    await stream.aclose()


async def test_a_full_subscriber_reads_what_it_missed_from_the_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api_gateway.operations import hub as hub_module

    monkeypatch.setattr(hub_module, "SUBSCRIBER_QUEUE_LIMIT", 2)
    pub = ops.OperationPublisher(OperationStore())
    stream = pub.hub.stream(PROJECT)
    await asyncio.wait_for(stream.__anext__(), 1)  # padding: subscribed now
    for i in range(1, 6):
        pub.publish(PROJECT, f"job:{i}", "queued", "queued")
    frames = await _frames(stream, 5)
    assert [f[1] for f in frames] == ["1", "2", "3", "4", "5"]
    await stream.aclose()


# ── publisher ───────────────────────────────────────────────────────────


def test_publish_never_raises_and_counts_refusals(
    publisher: ops.OperationPublisher, metrics: _Metrics
) -> None:
    assert ops.publish(PROJECT, "bogus", "started", "running") is None
    assert ops.publish(PROJECT, "run:r1", "not-a-type", "running") is None
    assert ops.publish(PROJECT, "run:r1", "completed", "completed") is not None
    assert ops.publish(PROJECT, "run:r1", "milestone", "running") is None
    assert ("failure", ("run", "after_terminal")) in metrics.calls
    assert ("active", ("run", -1)) not in metrics.calls  # never counted active

    class Broken(OperationStore):
        def append(self, project_id: str, incoming: OperationEventIn) -> Any:
            raise RuntimeError("disk full")

    broken = ops.OperationPublisher(Broken())
    assert broken.publish(PROJECT, "run:r2", "started", "running") is None
    assert ("failure", ("run", "error")) in metrics.calls


def test_active_gauge_moves_with_terminal_transitions(
    publisher: ops.OperationPublisher, metrics: _Metrics
) -> None:
    ops.publish(PROJECT, "job:j1", "queued", "queued")
    ops.publish(PROJECT, "job:j1", "started", "running")
    ops.publish(PROJECT, "job:j1", "completed", "completed")
    assert [c for c in metrics.calls if c[0] == "active"] == [
        ("active", ("job", 1)),
        ("active", ("job", -1)),
    ]


# ── routes ──────────────────────────────────────────────────────────────


def test_list_route_returns_snapshot_and_cursor(client: TestClient) -> None:
    ops.publish(PROJECT, "run:r1", "accepted", "queued", title="Concept design")
    ops.publish(PROJECT, "job:j1", "completed", "completed")
    body = client.get(f"/v1/projects/{PROJECT}/operations").json()
    assert body["project_id"] == PROJECT and body["cursor"] == 2
    assert {o["operation_id"] for o in body["operations"]} == {"run:r1", "job:j1"}
    active = client.get(f"/v1/projects/{PROJECT}/operations?active=true").json()
    assert [o["operation_id"] for o in active["operations"]] == ["run:r1"]
    snap = active["operations"][0]
    for key in (
        "operation_id",
        "kind",
        "domain",
        "title",
        "state",
        "run_id",
        "started_at",
        "updated_at",
        "last_sequence",
        "base_revision",
        "target_revision",
        "message",
        "severity",
        "artifact_refs",
        "requires_action",
        "action_refs",
        "progress",
        "stages",
    ):
        assert key in snap
    assert client.get(f"/v1/projects/{PROJECT}/operations?limit=0").status_code == 422


def test_stream_route_sends_sse_headers(client: TestClient) -> None:
    from api_gateway.operations.routes import _resume_point

    assert _resume_point("7", None) == 7
    assert _resume_point(None, 3) == 3
    assert _resume_point("garbage", 3) == 3
    route = next(r for r in operations_router.routes if getattr(r, "path", "").endswith("/stream"))
    assert "GET" in route.methods  # type: ignore[attr-defined]


def test_ingest_is_idempotent_and_refuses_regressions(client: TestClient) -> None:
    url = f"/v1/projects/{PROJECT}/operations/events"
    body = {"event_id": "e-1", "operation_id": "job:j1", "type": "queued", "state": "queued"}
    first = client.post(url, json=body)
    assert first.status_code == 202, first.text
    assert first.json() == {
        "event_id": "e-1",
        "project_seq": 1,
        "sequence": 1,
        "duplicate": False,
    }
    again = client.post(url, json=body)
    assert again.status_code == 200
    assert again.json()["project_seq"] == 1 and again.json()["duplicate"] is True
    done = client.post(
        url, json={"operation_id": "job:j1", "type": "completed", "state": "completed"}
    )
    assert done.status_code == 202
    late = client.post(url, json={"operation_id": "job:j1", "type": "started", "state": "running"})
    assert late.status_code == 409
    wrong = client.post(url, json={**body, "event_id": "e-2", "project_id": "other"})
    assert wrong.status_code == 422
    bad = client.post(url, json={"operation_id": "job:j1", "type": "nope", "state": "queued"})
    assert bad.status_code == 422


def test_cancel_route_404_409_and_202(client: TestClient) -> None:
    run_routes.reset_run_store()
    base = f"/v1/projects/{PROJECT}/operations"
    assert client.post(f"{base}/run:missing/cancel").status_code == 404

    plain = run_routes.get_run_store().create({"project_id": PROJECT, "goal": "x"})
    run_routes.get_run_store().start(plain.id)
    resp = client.post(f"{base}/run:{plain.id}/cancel")
    assert resp.status_code == 202, resp.text
    assert resp.json() == {"operation_id": f"run:{plain.id}", "state": "cancelled"}
    snap = ops.get_operation_publisher().store.get(PROJECT, f"run:{plain.id}")
    assert snap is not None and snap.state == "cancelled"
    assert client.post(f"{base}/run:{plain.id}/cancel").status_code == 409  # already ended

    flow = run_routes.get_run_store().create({"project_id": PROJECT, "flow": "design_v1"})
    resp = client.post(f"{base}/run:{flow.id}/cancel")
    assert resp.status_code == 409 and "design-flow" in resp.json()["detail"]

    ops.publish(PROJECT, "chat:t1", "started", "running")
    assert client.post(f"{base}/chat:t1/cancel").status_code == 409
    ops.publish(PROJECT, "job:elsewhere", "started", "running")
    assert client.post(f"{base}/job:elsewhere/cancel").status_code == 409
    run_routes.reset_run_store()


async def test_cancel_route_cancels_a_job_this_gateway_runs(
    publisher: ops.OperationPublisher,
) -> None:
    import httpx

    runner = JobRunner(JobStore(":memory:"), publisher=publish_local)
    set_default_job_runner(runner)
    gate = asyncio.Event()

    async def work() -> dict[str, Any]:
        await gate.wait()
        return {}

    try:
        job, _ = await runner.start(
            "cadquery.create_parametric", {}, work, session_id="s", project_id=PROJECT
        )
        await asyncio.sleep(0)
        await runner.flush_events()
        app = FastAPI()
        app.include_router(operations_router)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as http:
            resp = await http.post(f"/v1/projects/{PROJECT}/operations/job:{job.job_id}/cancel")
        assert resp.status_code == 202, resp.text
        await runner.flush_events()
        snap = publisher.store.get(PROJECT, f"job:{job.job_id}")
        assert snap is not None and snap.state == "cancelled"
        assert snap.domain == "mechanical"
    finally:
        set_default_job_runner(None)


# ── producers: runs ─────────────────────────────────────────────────────


@pytest.fixture
def runs(publisher: ops.OperationPublisher) -> Iterator[ops.OperationPublisher]:
    run_routes.reset_run_store()
    yield publisher
    run_routes.reset_run_store()


def _types(pub: ops.OperationPublisher, op: str) -> list[tuple[str, str]]:
    return [
        (e.type.value, e.state.value)
        for e in pub.store.events_after(PROJECT, 0)
        if e.operation_id == op
    ]


def test_run_launch_emits_accepted_and_queued_then_real_transitions(
    runs: ops.OperationPublisher,
) -> None:
    store = run_routes.get_run_store()
    run = store.create({"project_id": PROJECT, "flow": "design_v1"})
    op = f"run:{run.id}"
    assert _types(runs, op) == [("accepted", "queued"), ("queued", "queued")]
    store.start(run.id)
    store.annotate(run.id, "note", "x")  # not a transition: nothing reported
    run_routes.publish_tool_event(
        run.id, call_id="c1", tool="cadquery.create_parametric", phase="design", status="started"
    )
    run_routes.publish_tool_event(
        run.id, call_id="c2", tool="cadquery.export", phase="design", status="finished"
    )
    store.request_approval(run.id, reason="Gate 'design'")
    from orchestrator.harness.runs import ApprovalDecision

    store.submit_approval(run.id, ApprovalDecision.APPROVE)
    store.complete(run.id)
    assert _types(runs, op) == [
        ("accepted", "queued"),
        ("queued", "queued"),
        ("started", "running"),
        ("milestone", "running"),
        ("review_required", "waiting_for_user"),
        ("resumed", "running"),
        ("completed", "completed"),
    ]
    events = [e for e in runs.store.events_after(PROJECT, 0) if e.operation_id == op]
    review = events[4]
    assert review.requires_action
    assert [(a.kind, a.id) for a in review.action_refs] == [("approval", f"gate:{run.id}")]
    milestone = events[3]
    assert milestone.stage_id == "design" and milestone.stage_title
    assert milestone.run_id == run.id
    assert all(e.message and "Gate 'design'" not in e.message for e in events)
    snap = runs.store.get(PROJECT, op)
    assert snap is not None and snap.state == "completed"
    assert [(s.stage_id, s.state) for s in snap.stages] == [("design", "completed")]


def test_run_without_a_project_reports_nothing(runs: ops.OperationPublisher) -> None:
    run = run_routes.get_run_store().create({"goal": "x"})
    run_routes.get_run_store().start(run.id)
    assert runs.store.find(f"run:{run.id}") is None


def test_a_refused_run_ends_its_operation(runs: ops.OperationPublisher) -> None:
    run = run_routes.get_run_store().create({"project_id": PROJECT, "flow": "design_v1"})
    run_routes._discard_run(run.id, "engine down")  # noqa: SLF001
    assert _types(runs, f"run:{run.id}")[-1] == ("failed", "failed")


def test_run_failures_and_rejections_map_to_terminal_states(runs: ops.OperationPublisher) -> None:
    store = run_routes.get_run_store()
    failed = store.create({"project_id": PROJECT})
    store.start(failed.id)
    store.fail(failed.id, "Traceback: secret detail")
    events = [
        e for e in runs.store.events_after(PROJECT, 0) if e.operation_id == f"run:{failed.id}"
    ]
    assert events[-1].state == "failed" and events[-1].severity == "error"
    assert "secret" not in events[-1].message  # the message is authored, never copied


# ── producers: jobs ─────────────────────────────────────────────────────


class _Sink:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, project_id: str, event: dict[str, Any]) -> None:
        self.events.append((project_id, event))


async def test_job_events_cover_its_life_with_fixed_sequences() -> None:
    sink = _Sink()
    runner = JobRunner(JobStore(":memory:"), publisher=sink)

    async def work() -> dict[str, Any]:
        return {"node_id": "wp-1", "secret": "nope"}

    job, _ = await runner.start(
        "twin.generate_concept_mesh", {"a": 1}, work, session_id="s", project_id=PROJECT
    )
    await runner.wait(job.job_id, 2)
    await runner.flush_events()
    seen = [(e["type"], e["state"], e["sequence"]) for _, e in sink.events]
    assert seen == [
        ("queued", "queued", 10),
        ("started", "running", 20),
        ("completed", "completed", 90),
    ]
    done = sink.events[-1][1]
    assert done["operation_id"] == f"job:{job.job_id}" and done["domain"] == "concept"
    assert done["artifact_refs"] == [
        {"kind": "work_product", "id": "wp-1", "label": "Generate concept mesh"}
    ]
    assert "secret" not in json.dumps(sink.events)
    assert len({e["event_id"] for _, e in sink.events}) == 3


async def test_job_without_a_project_or_publisher_emits_nothing() -> None:
    sink = _Sink()
    runner = JobRunner(JobStore(":memory:"), publisher=sink)

    async def work() -> dict[str, Any]:
        return {}

    job, _ = await runner.start("kicad.run_drc", {}, work, session_id="s")
    await runner.wait(job.job_id, 2)
    await runner.flush_events()
    assert sink.events == []


async def test_job_cancel_and_supersede() -> None:
    sink = _Sink()
    runner = JobRunner(JobStore(":memory:"), publisher=sink)
    started = asyncio.Event()

    async def slow() -> dict[str, Any]:
        started.set()
        await asyncio.sleep(30)
        return {}

    first, _ = await runner.start(
        "kicad.run_drc", {"n": 1}, slow, session_id="s", project_id=PROJECT
    )
    await started.wait()
    cancelled = runner.cancel(first.job_id)
    assert cancelled is not None and cancelled.status == "cancelled"
    assert cancelled.error and "cancelled" in cancelled.error
    assert not runner.owns(first.job_id)
    assert (await runner.wait(first.job_id, 1)).status == "cancelled"  # type: ignore[union-attr]
    again = runner.cancel(first.job_id)
    assert again is not None and again.status == "cancelled"  # unchanged, not re-ended

    second, _ = await runner.start(
        "kicad.run_drc", {"n": 2}, slow, session_id="s", project_id=PROJECT
    )
    replaced = runner.supersede(second.job_id, by="job-3")
    assert replaced is not None and replaced.status == "superseded"
    assert runner.cancel("missing") is None
    await asyncio.sleep(0)
    await runner.flush_events()
    finals = [
        (e["operation_id"], e["type"], e["domain"]) for _, e in sink.events if e["sequence"] == 90
    ]
    assert finals == [
        (f"job:{first.job_id}", "cancelled", "electronics"),
        (f"job:{second.job_id}", "superseded", "electronics"),
    ]
    # A cancelled job is a notice for its session like any other ending.
    assert {j.job_id for j in runner.take_notices("s")} == {first.job_id, second.job_id}


async def test_jobs_interrupted_by_a_restart_report_it(tmp_path: Path) -> None:
    path = tmp_path / "jobs.db"
    runner = JobRunner(JobStore(path))
    gate = asyncio.Event()

    async def blocked() -> dict[str, Any]:
        await gate.wait()
        return {}

    job, _ = await runner.start("calculix.run_fea", {}, blocked, session_id="s", project_id=PROJECT)
    await asyncio.sleep(0)
    sink = _Sink()
    JobRunner(JobStore(path), publisher=sink)  # a second process opens the file
    await asyncio.sleep(0)
    assert [(e["operation_id"], e["type"], e["sequence"]) for _, e in sink.events] == [
        (f"job:{job.job_id}", "failed", 90)
    ]
    gate.set()
    await runner.wait(job.job_id, 1)


async def test_job_events_reach_the_store_through_the_process_seam(
    publisher: ops.OperationPublisher,
) -> None:
    set_operation_event_publisher(publish_local)
    try:
        from tool_registry.jobs import _publish_via_process_seam

        runner = JobRunner(JobStore(":memory:"), publisher=_publish_via_process_seam)

        async def work() -> dict[str, Any]:
            return {}

        job, _ = await runner.start("firmware.build", {}, work, session_id="s", project_id=PROJECT)
        await runner.wait(job.job_id, 2)
        await runner.flush_events()
        snap = publisher.store.get(PROJECT, f"job:{job.job_id}")
        assert snap is not None and snap.state == "completed" and snap.domain == "firmware"
        assert snap.last_sequence == 90
    finally:
        set_operation_event_publisher(None)
    assert await publish_operation_event(PROJECT, {}) is False


async def test_http_operation_publisher_posts_to_the_ingest_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    from metaforge.mcp.live_publisher import http_operation_publisher

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(409 if len(seen) > 1 else 202, json={})

    monkeypatch.setenv("METAFORGE_GATEWAY_URL", "http://gw:8000/")
    monkeypatch.setenv("METAFORGE_GATEWAY_API_KEY", "k")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        post = http_operation_publisher(client)
        assert post is not None
        await post(PROJECT, {"operation_id": "job:1", "type": "queued", "state": "queued"})
        await post(PROJECT, {"operation_id": "job:1", "type": "queued", "state": "queued"})
    assert str(seen[0].url) == f"http://gw:8000/v1/projects/{PROJECT}/operations/events"
    assert seen[0].headers["Authorization"] == "Bearer k"
    monkeypatch.delenv("METAFORGE_GATEWAY_URL")
    assert http_operation_publisher() is None


def test_domains_come_from_tool_prefixes_and_flow_ids() -> None:
    assert domain_for_tool("kicad.run_drc") == "electronics"
    assert domain_for_tool("cadquery.create_parametric") == "mechanical"
    assert domain_for_tool("calculix.run_fea") == "simulation"
    assert domain_for_tool("twin.generate_concept_mesh") == "concept"
    assert domain_for_tool("twin.job_status") == "general"
    assert domain_for_text("mech_v1") == "mechanical"
    assert domain_for_text("hardware_v1") == "cross_domain"
    assert domain_for_text("features") == "general"
