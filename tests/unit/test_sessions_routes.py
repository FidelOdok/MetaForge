"""Unit tests for the sessions REST endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from api_gateway.server import create_app
from orchestrator.workflow_dag import (
    InMemoryWorkflowEngine,
    StepStatus,
    WorkflowDefinition,
    WorkflowStep,
)


def _make_mock_scheduler() -> MagicMock:
    """Create a mock scheduler that won't fail on stop()."""
    sched = MagicMock()
    sched.stop = AsyncMock()
    return sched


@pytest.fixture
def engine() -> InMemoryWorkflowEngine:
    return InMemoryWorkflowEngine.create()


@pytest.fixture
def client(engine: InMemoryWorkflowEngine) -> TestClient:
    app = create_app(workflow_engine=engine, scheduler=_make_mock_scheduler())
    return TestClient(app)


class TestListSessions:
    def test_empty_when_no_runs(self, client: TestClient) -> None:
        resp = client.get("/api/v1/sessions")
        assert resp.status_code == 200
        body = resp.json()
        assert body["sessions"] == []
        assert body["total"] == 0

    @pytest.mark.anyio
    async def test_lists_runs_as_sessions(self, engine: InMemoryWorkflowEngine) -> None:
        defn = WorkflowDefinition(
            name="validate_stress",
            steps=[WorkflowStep(step_id="s1", agent_code="MECH", task_type="validate_stress")],
        )
        await engine.register_workflow(defn)
        run = await engine.start_run(defn.id)
        await engine.update_step(run.id, "s1", StepStatus.COMPLETED, result={"ok": True})

        app = create_app(workflow_engine=engine, scheduler=_make_mock_scheduler())
        with TestClient(app) as tc:
            resp = tc.get("/api/v1/sessions")
            assert resp.status_code == 200
            body = resp.json()
            assert body["total"] == 1
            session = body["sessions"][0]
            assert session["id"] == run.id
            assert session["agent_code"] == "MECH"
            assert session["task_type"] == "validate_stress"
            assert session["status"] == "completed"
            assert len(session["events"]) >= 1


class TestGetSession:
    @pytest.mark.anyio
    async def test_get_by_id(self, engine: InMemoryWorkflowEngine) -> None:
        defn = WorkflowDefinition(
            name="run_erc",
            steps=[WorkflowStep(step_id="s1", agent_code="EE", task_type="run_erc")],
        )
        await engine.register_workflow(defn)
        run = await engine.start_run(defn.id)

        app = create_app(workflow_engine=engine, scheduler=_make_mock_scheduler())
        with TestClient(app) as tc:
            resp = tc.get(f"/api/v1/sessions/{run.id}")
            assert resp.status_code == 200
            body = resp.json()
            assert body["id"] == run.id
            assert body["agent_code"] == "EE"

    def test_404_for_unknown_id(self, client: TestClient) -> None:
        resp = client.get("/api/v1/sessions/nonexistent")
        assert resp.status_code == 404
