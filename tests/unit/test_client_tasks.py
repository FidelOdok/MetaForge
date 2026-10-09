"""Client-mode phase tasks: the store, the runner and the routes (FORGE-581)."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_gateway.client_tasks import routes as task_routes
from api_gateway.runs import routes as run_routes
from api_gateway.runs.client_phase import (
    ClientPhaseBrain,
    ClientTaskCancelledError,
    HttpTaskChannel,
    LocalTaskChannel,
    brief_for,
    run_client_phase,
    run_client_phase_request,
    task_from_request,
)
from orchestrator.design_flow.client_tasks import (
    INTELLIGENCE_ENV,
    Intelligence,
    PhaseTask,
    SqliteClientTaskStore,
    TaskNotFoundError,
    TaskStateError,
    default_intelligence,
    parse_intelligence,
    task_id_for,
)
from orchestrator.design_flow.executor import FlowContext
from orchestrator.design_flow.frozen import FrozenGate, FrozenPhase
from orchestrator.design_flow.spec import Phase
from orchestrator.design_flow.temporal_flow import PhaseRequest


def _task(attempt: int = 1) -> PhaseTask:
    return PhaseTask(
        id=task_id_for("run-1", "needs", attempt),
        run_id="run-1",
        phase_id="needs",
        attempt=attempt,
        project_id="proj-1",
        brief={"title": "Needs"},
    )


# ── the mode ─────────────────────────────────────────────────────────────


class TestIntelligence:
    def test_parses_both_modes(self) -> None:
        assert parse_intelligence("client") is Intelligence.CLIENT
        assert parse_intelligence(" Server ") is Intelligence.SERVER

    def test_an_unknown_mode_is_refused_not_defaulted(self) -> None:
        with pytest.raises(ValueError, match="server, client"):
            parse_intelligence("local")

    def test_default_is_server(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(INTELLIGENCE_ENV, raising=False)
        assert default_intelligence() is Intelligence.SERVER

    def test_deployment_default_can_be_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(INTELLIGENCE_ENV, "client")
        assert default_intelligence() is Intelligence.CLIENT

    def test_a_typo_in_the_deployment_default_is_an_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(INTELLIGENCE_ENV, "clinet")
        with pytest.raises(ValueError):
            default_intelligence()

    def test_run_request_wins_over_the_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(INTELLIGENCE_ENV, "server")
        assert run_routes.resolve_run_intelligence({"intelligence": "client"}) is (
            Intelligence.CLIENT
        )
        assert run_routes.resolve_run_intelligence({}) is Intelligence.SERVER


# ── the store ────────────────────────────────────────────────────────────


class TestStore:
    def test_open_claim_submit(self) -> None:
        store = SqliteClientTaskStore()
        store.open_task(_task())
        claimed = store.claim("run-1:needs:1", "claude-code")
        assert claimed.status == "claimed"
        assert claimed.claimed_by == "claude-code"
        done = store.submit("run-1:needs:1", "claude-code", " recorded needs ", ["n-1"])
        assert done.status == "submitted"
        assert done.summary == "recorded needs"
        assert store.get("run-1:needs:1").artifacts == ["n-1"]

    def test_reopening_returns_the_task_already_there(self) -> None:
        store = SqliteClientTaskStore()
        store.open_task(_task())
        store.submit("run-1:needs:1", "codex", "done", [])
        again = store.open_task(_task())
        # A retried activity finds the submission the first attempt never read.
        assert again.status == "submitted"
        assert len(store.list_tasks()) == 1

    def test_a_cancelled_task_is_reopened_by_a_retry(self) -> None:
        store = SqliteClientTaskStore()
        store.open_task(_task())
        store.claim("run-1:needs:1", "codex")
        store.cancel("run-1:needs:1", "worker stopping")
        again = store.open_task(_task())
        assert again.status == "open"
        assert again.claimed_by is None

    def test_submitting_twice_is_refused(self) -> None:
        store = SqliteClientTaskStore()
        store.open_task(_task())
        store.submit("run-1:needs:1", "codex", "done", [])
        with pytest.raises(TaskStateError):
            store.submit("run-1:needs:1", "codex", "again", [])

    def test_an_empty_summary_is_refused(self) -> None:
        store = SqliteClientTaskStore()
        store.open_task(_task())
        with pytest.raises(TaskStateError, match="summary"):
            store.submit("run-1:needs:1", "codex", "   ", [])

    def test_a_finished_task_cannot_be_claimed_or_cancelled(self) -> None:
        store = SqliteClientTaskStore()
        store.open_task(_task())
        store.submit("run-1:needs:1", "codex", "done", [])
        with pytest.raises(TaskStateError):
            store.claim("run-1:needs:1", "other")
        assert store.cancel("run-1:needs:1", "late").status == "submitted"

    def test_unknown_task(self) -> None:
        with pytest.raises(TaskNotFoundError):
            SqliteClientTaskStore().get("nope")

    def test_list_filters(self) -> None:
        store = SqliteClientTaskStore()
        store.open_task(_task(1))
        store.open_task(_task(2))
        store.submit("run-1:needs:1", "codex", "done", [])
        assert [t.id for t in store.list_tasks(status="open")] == ["run-1:needs:2"]
        assert len(store.list_tasks(project_id="proj-1")) == 2
        assert store.list_tasks(run_id="other") == []

    def test_survives_a_reopen_of_the_file(self, tmp_path: Any) -> None:
        path = str(tmp_path / "tasks.db")
        SqliteClientTaskStore(path).open_task(_task())
        reopened = SqliteClientTaskStore(path)
        assert reopened.get("run-1:needs:1").brief == {"title": "Needs"}


# ── the brief and the runner ─────────────────────────────────────────────


def _frozen_phase() -> FrozenPhase:
    return FrozenPhase(
        id="needs",
        title="Stakeholder needs",
        objective="Record the needs",
        required_deliverables=["stakeholder_need"],
        disciplines=["systems"],
        gate=FrozenGate(name="needs_review"),
    )


def test_brief_carries_what_a_phase_brain_is_told() -> None:
    brief = brief_for(
        goal="a desk",
        phase=_frozen_phase(),
        flow_context="route: cnc",
        retry_feedback="missing need",
        prior=["intent recorded"],
        attempt=2,
        project_id="p",
    )
    assert brief["objective"] == "Record the needs"
    assert brief["required_deliverables"] == ["stakeholder_need"]
    assert brief["gate"] == "needs_review"
    assert brief["retry_feedback"] == "missing need"
    assert brief["prior_phases"] == ["intent recorded"]
    assert "phase.submit" in brief["how_to_submit"]


class _ScriptedChannel(LocalTaskChannel):
    """A local channel whose task gets submitted after a few polls."""

    def __init__(self, store: SqliteClientTaskStore, submit_after: int) -> None:
        super().__init__(store)
        self.store = store
        self.polls = 0
        self.submit_after = submit_after

    async def get(self, task_id: str) -> PhaseTask:
        self.polls += 1
        if self.polls == self.submit_after:
            self.store.submit(task_id, "claude-code", "recorded two needs", ["n-1", "n-2"])
        return await super().get(task_id)


async def _no_sleep(_: float) -> None:
    return None


def test_the_runner_waits_for_the_submission() -> None:
    store = SqliteClientTaskStore()
    channel = _ScriptedChannel(store, submit_after=3)
    summary, artifacts = asyncio.run(run_client_phase(channel, _task(), sleep=_no_sleep))
    assert summary == "recorded two needs"
    assert artifacts == ["n-1", "n-2"]
    assert channel.polls == 3


def test_a_task_withdrawn_while_waiting_ends_the_phase() -> None:
    store = SqliteClientTaskStore()

    class _Cancelling(LocalTaskChannel):
        async def get(self, task_id: str) -> PhaseTask:
            store.cancel(task_id, "run cancelled")
            return await super().get(task_id)

    with pytest.raises(ClientTaskCancelledError, match="run cancelled"):
        asyncio.run(run_client_phase(_Cancelling(store), _task(), sleep=_no_sleep))


def test_a_worker_that_stops_withdraws_the_task() -> None:
    store = SqliteClientTaskStore()

    async def scenario() -> None:
        waiting = asyncio.ensure_future(
            run_client_phase(LocalTaskChannel(store), _task(), poll_seconds=10)
        )
        await asyncio.sleep(0.01)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting

    asyncio.run(scenario())
    assert store.get("run-1:needs:1").status == "cancelled"


def test_the_worker_request_becomes_a_task_and_a_phase_result() -> None:
    request = PhaseRequest(
        run_id="run-1",
        goal="a desk",
        phase=_frozen_phase(),
        project_id="proj-1",
        attempt=2,
        intelligence="client",
    )
    task = task_from_request(request)
    assert task.id == "run-1:needs:2"
    store = SqliteClientTaskStore()
    store.open_task(task)
    store.submit(task.id, "codex", "done", ["n-1"])
    result = asyncio.run(run_client_phase_request(LocalTaskChannel(store), request))
    assert result.status == "completed"
    assert result.artifacts == ["n-1"]


def test_the_in_process_brain_hands_the_phase_to_the_client() -> None:
    store = SqliteClientTaskStore()
    channel = _ScriptedChannel(store, submit_after=1)
    brain = ClientPhaseBrain("run-1", channel)
    phase = Phase(id="needs", title="Needs", objective="Record the needs")
    outcome = asyncio.run(
        brain.run_phase(goal="a desk", phase=phase, context=FlowContext(goal="a desk"))
    )
    assert outcome.summary == "recorded two needs"
    assert store.get("run-1:needs:1").brief["objective"] == "Record the needs"


def test_http_channel_round_trip() -> None:
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        assert request.headers["Authorization"] == "Bearer k"
        body = _task().as_dict()
        if request.method == "GET":
            body.update(status="submitted", summary="ok")
        return httpx.Response(201 if request.method == "POST" else 200, json=body)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    channel = HttpTaskChannel("http://gw", api_key="k", client=client)
    summary, _ = asyncio.run(run_client_phase(channel, _task(), sleep=_no_sleep))
    assert summary == "ok"
    assert seen == [("POST", "/v1/client-tasks"), ("GET", "/v1/client-tasks/run-1:needs:1")]


def test_http_channel_refusal_is_not_retried() -> None:
    from temporalio.exceptions import ApplicationError

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(409, json={"detail": "server"}))
    )
    with pytest.raises(ApplicationError) as err:
        asyncio.run(HttpTaskChannel("http://gw", client=client).open(_task()))
    assert err.value.non_retryable


# ── the routes ───────────────────────────────────────────────────────────


@pytest.fixture
def api() -> Any:
    run_routes.reset_run_store()
    task_routes.init_client_task_store(SqliteClientTaskStore())
    app = FastAPI()
    app.include_router(task_routes.router)
    yield TestClient(app)
    task_routes.init_client_task_store(None)
    run_routes.reset_run_store()


def _run(intelligence: str) -> str:
    run = run_routes.get_run_store().create({"kind": "design_flow", "intelligence": intelligence})
    return run.id


def test_routes_full_cycle(api: TestClient) -> None:
    run_id = _run("client")
    opened = api.post(
        "/v1/client-tasks",
        json={"runId": run_id, "phaseId": "needs", "projectId": "p", "brief": {"title": "N"}},
    )
    assert opened.status_code == 201
    task_id = opened.json()["id"]
    listed = api.get("/v1/client-tasks", params={"project_id": "p"}).json()["tasks"]
    assert [t["id"] for t in listed] == [task_id]
    assert "brief" not in listed[0]
    claimed = api.post(
        f"/v1/client-tasks/{task_id}/claim", json={}, headers={"X-MetaForge-Agent": "codex"}
    )
    assert claimed.json()["claimed_by"] == "codex"
    assert claimed.json()["brief"] == {"title": "N"}
    done = api.post(f"/v1/client-tasks/{task_id}/submit", json={"summary": "did it"})
    assert done.json()["status"] == "submitted"
    again = api.post(f"/v1/client-tasks/{task_id}/submit", json={"summary": "again"})
    assert again.status_code == 409


def test_a_server_mode_run_takes_no_tasks(api: TestClient) -> None:
    resp = api.post("/v1/client-tasks", json={"runId": _run("server"), "phaseId": "needs"})
    assert resp.status_code == 409


def test_an_unknown_run_takes_no_tasks(api: TestClient) -> None:
    resp = api.post("/v1/client-tasks", json={"runId": "nope", "phaseId": "needs"})
    assert resp.status_code == 404


def test_unknown_task_is_404(api: TestClient) -> None:
    assert api.get("/v1/client-tasks/nope").status_code == 404
    assert api.post("/v1/client-tasks/nope/claim", json={}).status_code == 404
