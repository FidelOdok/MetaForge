"""A held approval nobody is waiting for must not stay pending (FORGE-466).

The sidecar parked a write in the gateway's ledger and gave up after 180 s,
but the entry stayed ``awaiting_approval``. The dashboard went on offering
it, and approving it recorded an approval for a call that was already over.

So: the waiting side closes its hold however it stops waiting (``timed_out``
on its window, ``canceled`` on cancellation), the gateway expires holds past
a stored deadline for a waiter that died silently, and answering a closed
hold is a 409 that says why.

The sidecar tests drive the real gateway router over ASGI, as
``test_remote_approval_gate`` does: a double for the gateway would pass while
proving nothing about the ledger a reviewer actually reads.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from mcp_core.guardrails import ApprovalAsk, ApprovalOutcome, Caller
from metaforge.mcp.remote_approvals import build_remote_approval_gate
from observability.metrics import MetricsCollector
from orchestrator.harness.runs import (
    ApprovalWait,
    InMemoryRunStore,
    RunStatus,
    await_approval_decision,
)

ENDPOINT = "/v1/chat/tool_approvals"


class _FakeMetrics(MetricsCollector):
    def __init__(self) -> None:
        super().__init__()
        self.resolutions: list[tuple[str, str, str]] = []

    def record_tool_approval_resolution(self, outcome: str, trigger: str, result: str) -> None:
        self.resolutions.append((outcome, trigger, result))


class _Clock:
    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def gateway_metrics():
    from api_gateway.chat import tool_approvals

    metrics = _FakeMetrics()
    tool_approvals.set_approval_metrics(metrics)
    yield metrics
    tool_approvals.set_approval_metrics(None)


@pytest.fixture
def clock(gateway_metrics):
    from api_gateway.chat.tool_approvals import reset_approval_store

    fake = _Clock()
    reset_approval_store(clock=fake)
    yield fake
    reset_approval_store()


@pytest.fixture
def app(clock):
    from fastapi import FastAPI

    from api_gateway.chat.tool_approvals import router

    application = FastAPI()
    application.include_router(router)
    return application


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway.test")


def _ask() -> ApprovalAsk:
    return ApprovalAsk(
        tool_id="twin.record_decision",
        arguments={"title": "Use aluminium"},
        caller=Caller.UNTRUSTED,
        reason="writes; held for approval (untrusted caller)",
        session_id="sess-1",
        project="proj-1",
    )


def _store():
    from api_gateway.chat.tool_approvals import get_approval_store

    return get_approval_store()


def _only_run():
    runs = _store().list()
    assert len(runs) == 1
    return runs[0]


# ── the waiting side closes its own hold ────────────────────────────────


@pytest.mark.asyncio
class TestTheSidecarClosesWhatItOpened:
    async def test_a_held_call_that_times_out_leaves_the_entry_timed_out(self, app) -> None:
        """The observed bug: the sidecar timed out, the ledger said pending."""
        metrics = _FakeMetrics()
        async with _client(app) as http:
            gate = build_remote_approval_gate(
                "http://gateway.test",
                timeout_seconds=0.2,
                poll_interval=0.05,
                client=http,
                metrics=metrics,
            )
            resolution = await gate(_ask())

            assert resolution.outcome is ApprovalOutcome.TIMED_OUT
            run = _only_run()
            assert run.status is RunStatus.TIMED_OUT
            assert resolution.approval_id == run.id
            pending = (await http.get(ENDPOINT)).json()["runs"]
            assert pending == [], "the dashboard still offers a call nobody is waiting for"
        assert metrics.resolutions == [("timed_out", "waiter", "resolved")]

    async def test_a_cancelled_call_leaves_the_entry_canceled(self, app) -> None:
        """A client disconnect reaches the gate as a cancellation."""
        metrics = _FakeMetrics()
        async with _client(app) as http:
            gate = build_remote_approval_gate(
                "http://gateway.test",
                timeout_seconds=30.0,
                poll_interval=0.05,
                client=http,
                metrics=metrics,
            )
            task = asyncio.create_task(gate(_ask()))
            await asyncio.sleep(0.2)
            assert _only_run().status is RunStatus.AWAITING_APPROVAL

            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

            assert _only_run().status is RunStatus.CANCELED
            assert (await http.get(ENDPOINT)).json()["runs"] == []
        assert metrics.resolutions == [("canceled", "waiter", "resolved")]

    async def test_the_hold_carries_a_deadline_from_the_waiters_window(self, app, clock) -> None:
        async with _client(app) as http:
            created = await http.post(
                ENDPOINT,
                json={"tool": "twin.record_decision", "reason": "held", "timeout_seconds": 60},
            )
        assert created.status_code == 201
        deadline = created.json()["approval_deadline"]
        # Window plus grace, so the gateway never races a live waiter.
        assert deadline == pytest.approx(clock.now + 60 + 30)

    async def test_a_failure_to_close_is_counted_not_raised(self) -> None:
        """Best-effort: the tool call still gets its TIMED_OUT answer."""
        metrics = _FakeMetrics()

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST" and request.url.path == ENDPOINT:
                return httpx.Response(201, json={"id": "run_x", "status": "awaiting_approval"})
            if request.url.path.endswith("/resolve"):
                return httpx.Response(503, json={"detail": "down"})
            return httpx.Response(200, json={"id": "run_x", "status": "awaiting_approval"})

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="http://gateway.test"
        ) as http:
            gate = build_remote_approval_gate(
                "http://gateway.test",
                timeout_seconds=0.1,
                poll_interval=0.05,
                client=http,
                metrics=metrics,
            )
            resolution = await gate(_ask())

        assert resolution.outcome is ApprovalOutcome.TIMED_OUT
        assert metrics.resolutions == [("timed_out", "waiter", "failed")]

    async def test_a_decision_landing_as_the_window_closes_is_honoured(self) -> None:
        """The close is refused (409, decided), so the decision is read back."""
        metrics = _FakeMetrics()
        state = {"status": "awaiting_approval"}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST" and request.url.path == ENDPOINT:
                return httpx.Response(201, json={"id": "run_x", "status": "awaiting_approval"})
            if request.url.path.endswith("/resolve"):
                # Approved in the instant between the last poll and the close.
                state["status"] = "running"
                return httpx.Response(409, json={"detail": "already decided (running)"})
            body = {"id": "run_x", "status": state["status"]}
            if state["status"] == "running":
                body["approved_by"] = "local:dashboard"
            return httpx.Response(200, json=body)

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="http://gateway.test"
        ) as http:
            gate = build_remote_approval_gate(
                "http://gateway.test",
                timeout_seconds=0.1,
                poll_interval=0.05,
                client=http,
                metrics=metrics,
            )
            resolution = await gate(_ask())

        assert resolution.outcome is ApprovalOutcome.APPROVED
        assert resolution.approver is not None
        assert resolution.approver.actor_id == "local:dashboard"
        assert metrics.resolutions == [("timed_out", "waiter", "decided")]


# ── the gateway expires what nobody closed ──────────────────────────────


@pytest.mark.asyncio
class TestTheGatewayExpiresOrphanedHolds:
    async def test_a_hold_past_its_deadline_is_expired_on_read(
        self, app, clock, gateway_metrics
    ) -> None:
        """The sidecar crashed: nothing will ever close this one but us."""
        async with _client(app) as http:
            created = await http.post(
                ENDPOINT,
                json={"tool": "twin.record_decision", "reason": "held", "timeout_seconds": 10},
            )
            run_id = created.json()["id"]
            assert len((await http.get(ENDPOINT)).json()["runs"]) == 1

            clock.now += 10 + 30 + 1
            assert (await http.get(ENDPOINT)).json()["runs"] == []
            fetched = (await http.get(f"{ENDPOINT}/{run_id}")).json()

        assert fetched["status"] == "timed_out"
        assert "expired" in fetched["error"]
        assert gateway_metrics.resolutions == [("timed_out", "deadline", "resolved")]

    async def test_a_hold_inside_its_deadline_is_left_alone(self, app, clock) -> None:
        async with _client(app) as http:
            await http.post(
                ENDPOINT,
                json={"tool": "twin.record_decision", "reason": "held", "timeout_seconds": 10},
            )
            clock.now += 10 + 29
            assert len((await http.get(ENDPOINT)).json()["runs"]) == 1


# ── answering a closed hold ─────────────────────────────────────────────


@pytest.mark.asyncio
class TestAnsweringAClosedHold:
    async def _hold(self, http: httpx.AsyncClient) -> str:
        created = await http.post(ENDPOINT, json={"tool": "twin.record_decision", "reason": "held"})
        return str(created.json()["id"])

    async def test_approving_a_timed_out_hold_is_409_with_the_reason(self, app) -> None:
        async with _client(app) as http:
            run_id = await self._hold(http)
            await http.post(f"{ENDPOINT}/{run_id}/resolve", json={"outcome": "timed_out"})

            response = await http.post(f"{ENDPOINT}/{run_id}", json={"decision": "approve"})

        assert response.status_code == 409
        assert "timed_out" in response.json()["detail"]
        run = _store().get(run_id)
        assert run.status is RunStatus.TIMED_OUT
        assert run.approved_by is None, "an approval was recorded for a dead call"

    async def test_approving_an_expired_hold_is_409(self, app, clock) -> None:
        async with _client(app) as http:
            run_id = await self._hold(http)
            clock.now += 10_000
            response = await http.post(f"{ENDPOINT}/{run_id}", json={"decision": "approve"})
        assert response.status_code == 409
        assert "timed_out" in response.json()["detail"]

    async def test_rejecting_a_canceled_hold_is_409(self, app) -> None:
        async with _client(app) as http:
            run_id = await self._hold(http)
            await http.post(f"{ENDPOINT}/{run_id}/resolve", json={"outcome": "canceled"})
            response = await http.post(f"{ENDPOINT}/{run_id}", json={"decision": "reject"})
        assert response.status_code == 409
        assert "canceled" in response.json()["detail"]

    async def test_closing_is_idempotent(self, app) -> None:
        async with _client(app) as http:
            run_id = await self._hold(http)
            first = await http.post(f"{ENDPOINT}/{run_id}/resolve", json={"outcome": "timed_out"})
            again = await http.post(f"{ENDPOINT}/{run_id}/resolve", json={"outcome": "canceled"})
        assert first.status_code == again.status_code == 200
        # The second close does not rewrite the first.
        assert again.json()["status"] == "timed_out"

    async def test_closing_a_decided_hold_is_409_and_keeps_the_decision(self, app) -> None:
        async with _client(app) as http:
            run_id = await self._hold(http)
            await http.post(f"{ENDPOINT}/{run_id}", json={"decision": "approve"})
            response = await http.post(
                f"{ENDPOINT}/{run_id}/resolve", json={"outcome": "timed_out"}
            )
        assert response.status_code == 409
        assert _store().get(run_id).status is RunStatus.RUNNING

    async def test_closing_an_unknown_hold_is_404(self, app) -> None:
        async with _client(app) as http:
            response = await http.post(f"{ENDPOINT}/nope/resolve", json={"outcome": "timed_out"})
        assert response.status_code == 404


# ── the in-process wait closes its hold too ─────────────────────────────


@pytest.mark.asyncio
class TestTheInProcessWait:
    def _held(self) -> tuple[InMemoryRunStore, str]:
        store = InMemoryRunStore()
        run = store.create({"tool": "twin.record_decision"})
        store.start(run.id)
        store.request_approval(run.id, reason="held")
        return store, run.id

    async def test_a_timeout_leaves_the_run_timed_out_not_rejected(self) -> None:
        store, run_id = self._held()
        wait = await await_approval_decision(
            store, run_id, timeout_seconds=0.05, poll_interval=0.01, sleep=asyncio.sleep
        )
        assert wait is ApprovalWait.TIMED_OUT
        run = store.get(run_id)
        assert run.status is RunStatus.TIMED_OUT
        assert run.approved_by is None

    async def test_a_cancelled_wait_leaves_the_run_canceled(self) -> None:
        store, run_id = self._held()
        task = asyncio.create_task(
            await_approval_decision(
                store, run_id, timeout_seconds=30, poll_interval=0.01, sleep=asyncio.sleep
            )
        )
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert store.get(run_id).status is RunStatus.CANCELED

    async def test_a_hold_closed_elsewhere_ends_the_wait(self) -> None:
        store, run_id = self._held()
        store.time_out(run_id)
        wait = await await_approval_decision(
            store, run_id, timeout_seconds=30, poll_interval=0.01, sleep=asyncio.sleep
        )
        assert wait is ApprovalWait.TIMED_OUT

    async def test_expire_overdue_only_touches_pending_runs_past_deadline(self) -> None:
        clock = _Clock()
        store = InMemoryRunStore(clock=clock)
        overdue = store.create({})
        store.start(overdue.id)
        store.request_approval(overdue.id, deadline=clock.now + 5)
        no_deadline = store.create({})
        store.start(no_deadline.id)
        store.request_approval(no_deadline.id)

        clock.now += 6
        expired = store.expire_overdue()

        assert [r.id for r in expired] == [overdue.id]
        assert store.get(overdue.id).status is RunStatus.TIMED_OUT
        assert store.get(no_deadline.id).status is RunStatus.AWAITING_APPROVAL
