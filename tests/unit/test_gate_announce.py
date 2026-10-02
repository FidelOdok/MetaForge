"""An opened design-flow gate is announced, never approved (FORGE-489).

Live, every gate logged ``no announcer wired; nobody will be told this run is
waiting``. These pin: the worker wires a real announcer, the gateway route
moves the run's record to ``awaiting_approval`` (the Approvals page's source)
without approving it, and announced / unannounced / failed are counted.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from api_gateway.runs import routes as run_routes
from api_gateway.runs.gate_announce import http_gate_announcer
from orchestrator.design_flow import temporal_activities
from orchestrator.design_flow.temporal_activities import DesignFlowActivities
from orchestrator.harness.runs import RunStatus


class _Metrics:
    def __init__(self) -> None:
        self.outcomes: list[str] = []

    def record_design_flow_gate_announce(self, outcome: str) -> None:
        self.outcomes.append(outcome)


@pytest.fixture
def metrics(monkeypatch: pytest.MonkeyPatch) -> _Metrics:
    m = _Metrics()
    monkeypatch.setattr(temporal_activities, "collector_for", lambda _name: m)
    return m


async def _phase(_req: Any) -> Any:  # pragma: no cover - never run here
    raise AssertionError


PAYLOAD = {"run_id": "run_1", "gate": "Intent sign-off", "reason": "ready"}


class TestActivity:
    async def test_unwired_announcer_warns_and_counts(self, metrics: _Metrics) -> None:
        acts = DesignFlowActivities(phase_runner=_phase)
        with capture_logs() as logs:
            await acts.announce_gate(PAYLOAD)
        assert metrics.outcomes == ["unannounced"]
        assert any(e["event"] == "design_flow_gate_unannounced" for e in logs)

    async def test_wired_announcer_is_called_and_counted_without_warning(
        self, metrics: _Metrics
    ) -> None:
        seen: list[tuple[str, str, str]] = []

        async def announcer(run_id: str, gate: str, reason: str) -> None:
            seen.append((run_id, gate, reason))

        acts = DesignFlowActivities(phase_runner=_phase, gate_announcer=announcer)
        with capture_logs() as logs:
            await acts.announce_gate(PAYLOAD)
        assert seen == [("run_1", "Intent sign-off", "ready")]
        assert metrics.outcomes == ["announced"]
        assert not any(e["event"] == "design_flow_gate_unannounced" for e in logs)

    async def test_failing_announcer_is_an_alarm_not_a_failed_gate(self, metrics: _Metrics) -> None:
        async def announcer(*_a: str) -> None:
            raise RuntimeError("gateway down")

        acts = DesignFlowActivities(phase_runner=_phase, gate_announcer=announcer)
        with capture_logs() as logs:
            await acts.announce_gate(PAYLOAD)
        assert metrics.outcomes == ["failed"]
        assert any(e["event"] == "design_flow_gate_announce_failed" for e in logs)


class TestWorkerWiring:
    def test_worker_activities_carry_an_announcer(self) -> None:
        from api_gateway.runs.flow_worker import build_activities

        assert build_activities().gate_announcer is not None

    async def test_http_announcer_posts_to_the_gateway(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("METAFORGE_GATEWAY_URL", "http://gw:8000/")
        monkeypatch.setenv("METAFORGE_GATEWAY_API_KEY", "k")
        captured: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["auth"] = request.headers.get("authorization")
            return httpx.Response(200, json={})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await http_gate_announcer(client)("run_1", "G", "why")
        assert captured == {"url": "http://gw:8000/v1/runs/run_1/gate-opened", "auth": "Bearer k"}

    async def test_http_announcer_raises_on_a_refusal(self) -> None:
        def handler(_r: httpx.Request) -> httpx.Response:
            return httpx.Response(503)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(httpx.HTTPStatusError):
                await http_gate_announcer(client)("run_1", "G", "why")


class TestGatewayRoute:
    @pytest.fixture
    def client(self, monkeypatch: pytest.MonkeyPatch) -> TestClient:
        run_routes.reset_run_store()

        async def no_engine(run: Any) -> Any:
            return run

        monkeypatch.setattr(run_routes, "_reconcile_run", no_engine)
        app = FastAPI()
        app.include_router(run_routes.router)
        return TestClient(app)

    def test_open_gate_puts_the_run_on_the_approvals_list_without_approving(
        self, client: TestClient
    ) -> None:
        run = run_routes._store.create({"flow": "hardware_v1"}, run_id="run_1")
        run_routes._store.start(run.id)

        resp = client.post(
            "/v1/runs/run_1/gate-opened", json={"gate": "Intent sign-off", "reason": "ready"}
        )

        assert resp.status_code == 200
        assert resp.json()["status"] == RunStatus.AWAITING_APPROVAL.value
        assert resp.json()["approval_reason"] == "Gate 'Intent sign-off': ready"
        listed = client.get("/v1/runs").json()["runs"]
        assert [r["status"] for r in listed] == ["awaiting_approval"]
        # Announcing is not deciding: nothing was approved or rejected.
        assert run_routes._store.get("run_1").status is RunStatus.AWAITING_APPROVAL

    def test_announcing_twice_is_harmless(self, client: TestClient) -> None:
        run = run_routes._store.create({"flow": "hardware_v1"}, run_id="run_1")
        run_routes._store.start(run.id)
        body = {"gate": "G", "reason": "r"}
        assert client.post("/v1/runs/run_1/gate-opened", json=body).status_code == 200
        assert client.post("/v1/runs/run_1/gate-opened", json=body).status_code == 200

    def test_unknown_run_is_404(self, client: TestClient) -> None:
        resp = client.post("/v1/runs/nope/gate-opened", json={"gate": "G"})
        assert resp.status_code == 404
