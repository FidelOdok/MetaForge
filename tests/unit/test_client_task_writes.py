"""Scoped writes for a client working a claimed phase task (FORGE-584).

Driven through the real sidecar HTTP app against a real gateway app (runs and
client-task routers), like the service-caller tests: what matters is what the
sidecar decides from what the gateway says.

The grant is off unless the owner turns it on. When on, the person approves
``phase.claim`` once (it is still a held write), and from then on that
session's writes inside the run's project run without a hold, within the
service worker's bounds, until the task is submitted or the run moves on.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

import api_gateway.runs.routes as run_routes
from api_gateway.client_tasks import routes as task_routes
from api_gateway.design_flows.mcp_bindings import make_client_task_service
from mcp_core.context import (
    HEADER_PROJECT,
    HEADER_SESSION,
    McpCallContext,
    TaskBinding,
    bind_current_session_task,
    bound_task,
    clear_session_task,
    reset_session_tasks,
    with_context,
)
from mcp_core.guardrails import ApprovalAsk, ApprovalOutcome, Caller, decide
from metaforge.mcp.__main__ import _client_task_verifier_from_env, build_http_app
from metaforge.mcp.client_task_grant import (
    CLIENT_TASK_WRITES_ENV,
    ClientTaskNotConfirmedError,
    GatewayClientTaskVerifier,
)
from metaforge.mcp.server import UnifiedMcpServer
from orchestrator.design_flow.client_tasks import PhaseTask, SqliteClientTaskStore, task_id_for
from tool_registry.mcp_server.handlers import ToolManifest
from tool_registry.mcp_server.server import McpToolServer
from tool_registry.tools.design_flow.adapter import DesignFlowServer

PROJECT = str(uuid.uuid4())
OTHER_PROJECT = str(uuid.uuid4())
SESSION = str(uuid.uuid4())
OTHER_SESSION = str(uuid.uuid4())
WRITE = "twin.record_engineering_entity"


def _manifest(tool_id: str) -> ToolManifest:
    return ToolManifest(
        tool_id=tool_id,
        adapter_id=tool_id.split(".")[0],
        name=tool_id,
        description="stub",
        capability="test",
    )


class _Stub(McpToolServer):
    def __init__(self, adapter_id: str, tools: tuple[str, ...]) -> None:
        super().__init__(adapter_id=adapter_id, version="0.1.0")
        self.calls: list[str] = []
        for tool_id in tools:
            self.register_tool(_manifest(tool_id), self._handler(tool_id))

    def _handler(self, tool_id: str) -> Any:
        async def run(args: dict[str, Any]) -> dict[str, Any]:
            self.calls.append(tool_id)
            return {"ok": True}

        return run


class _Gate:
    """A person who approves taking the task and refuses everything else."""

    def __init__(self) -> None:
        self.asks: list[str] = []

    async def __call__(self, ask: ApprovalAsk) -> ApprovalOutcome:
        self.asks.append(ask.tool_id)
        return (
            ApprovalOutcome.APPROVED if ask.tool_id == "phase.claim" else (ApprovalOutcome.REJECTED)
        )


@pytest.fixture(autouse=True)
def _fresh() -> Any:
    run_routes.reset_run_store()
    task_routes.init_client_task_store(SqliteClientTaskStore())
    reset_session_tasks()
    yield
    run_routes.reset_run_store()
    task_routes.init_client_task_store(None)
    reset_session_tasks()


@pytest.fixture
def gateway_http() -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(run_routes.router)
    app.include_router(task_routes.router)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw.test")


def _client_run(*, intelligence: str = "client", project: str = PROJECT) -> tuple[str, str]:
    store = run_routes.get_run_store()
    run = store.create({"kind": "design_flow", "project_id": project, "intelligence": intelligence})
    store.start(run.id)
    task = task_routes.get_client_task_store().open_task(
        PhaseTask(
            id=task_id_for(run.id, "needs", 1),
            run_id=run.id,
            phase_id="needs",
            project_id=project,
            brief={"title": "Needs"},
        )
    )
    return run.id, task.id


class _Sidecar:
    def __init__(self, verifier: Any) -> None:
        self.twin = _Stub("twin", (WRITE, "twin.attempt_promotion"))
        self.project = _Stub("project", ("project.create",))
        self.flows = DesignFlowServer(client_tasks=make_client_task_service())
        self.gate = _Gate()
        self.server = UnifiedMcpServer(
            [self.twin, self.project, self.flows],
            caller=Caller.UNTRUSTED,
            approval_gate=self.gate,
        )
        app = build_http_app(self.server, enable_sse=False)
        # build_http_app reads the owner's switch from the environment (off
        # here); the test then sets the grant it is about.
        self.server.attach_client_task_grant(verifier)
        self.client = TestClient(app)

    def call(
        self,
        tool_id: str,
        arguments: dict[str, Any] | None = None,
        *,
        session: str | None = SESSION,
        project: str | None = PROJECT,
    ) -> dict[str, Any]:
        headers: dict[str, str] = {}
        if session:
            headers[HEADER_SESSION] = session
        if project:
            headers[HEADER_PROJECT] = project
        response = self.client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": tool_id, "arguments": arguments or {}},
            },
            headers=headers,
        )
        assert response.status_code == 200, response.text
        body: dict[str, Any] = response.json()
        return body


def _verifier(gateway_http: httpx.AsyncClient) -> GatewayClientTaskVerifier:
    return GatewayClientTaskVerifier("http://gw.test", client=gateway_http, ttl_seconds=0)


def _claimed(sidecar: _Sidecar, task_id: str) -> None:
    body = sidecar.call("phase.claim", {"task_id": task_id})
    assert "error" not in body, body


# ── the grant ────────────────────────────────────────────────────────────


class TestTheGrant:
    def test_after_the_claim_writes_run_without_a_hold(self, gateway_http: Any) -> None:
        _, task_id = _client_run()
        sidecar = _Sidecar(_verifier(gateway_http))
        _claimed(sidecar, task_id)

        body = sidecar.call(WRITE, {"title": "need"})

        assert "error" not in body, body
        assert sidecar.twin.calls == [WRITE]
        # The person was asked once: to let the client take the phase.
        assert sidecar.gate.asks == ["phase.claim"]

    def test_switched_off_the_same_write_is_held(self, gateway_http: Any) -> None:
        _, task_id = _client_run()
        sidecar = _Sidecar(None)
        _claimed(sidecar, task_id)

        body = sidecar.call(WRITE)

        assert body["error"]["data"]["code"] == "approval_required"
        assert sidecar.twin.calls == []

    def test_another_session_gets_nothing(self, gateway_http: Any) -> None:
        _, task_id = _client_run()
        sidecar = _Sidecar(_verifier(gateway_http))
        _claimed(sidecar, task_id)

        body = sidecar.call(WRITE, session=OTHER_SESSION)

        assert body["error"]["data"]["code"] == "approval_required"

    def test_submitting_ends_the_grant(self, gateway_http: Any) -> None:
        _, task_id = _client_run()
        sidecar = _Sidecar(_verifier(gateway_http))
        _claimed(sidecar, task_id)
        submitted = sidecar.call("phase.submit", {"task_id": task_id, "summary": "done"})
        assert "error" not in submitted, submitted

        body = sidecar.call(WRITE)

        assert body["error"]["data"]["code"] == "approval_required"

    def test_a_run_that_stopped_running_ends_the_grant(self, gateway_http: Any) -> None:
        run_id, task_id = _client_run()
        sidecar = _Sidecar(_verifier(gateway_http))
        _claimed(sidecar, task_id)
        run_routes.get_run_store().fail(run_id, error="stopped")

        body = sidecar.call(WRITE)

        assert body["error"]["data"]["code"] == "approval_required"
        assert bound_task(uuid.UUID(SESSION)) is None


class TestTheBounds:
    def test_admin_tools_are_refused_not_held(self, gateway_http: Any) -> None:
        _, task_id = _client_run()
        sidecar = _Sidecar(_verifier(gateway_http))
        _claimed(sidecar, task_id)

        body = sidecar.call("project.create", {"name": "x"})

        assert "error" in body
        assert sidecar.project.calls == []
        assert sidecar.gate.asks == ["phase.claim"]

    def test_human_authority_is_refused(self, gateway_http: Any) -> None:
        _, task_id = _client_run()
        sidecar = _Sidecar(_verifier(gateway_http))
        _claimed(sidecar, task_id)

        body = sidecar.call("twin.attempt_promotion")

        assert "error" in body
        assert "twin.attempt_promotion" not in sidecar.twin.calls

    def test_naming_another_project_is_refused(self, gateway_http: Any) -> None:
        _, task_id = _client_run()
        sidecar = _Sidecar(_verifier(gateway_http))
        _claimed(sidecar, task_id)

        body = sidecar.call(WRITE, {"project_id": OTHER_PROJECT})

        assert "error" in body
        assert sidecar.twin.calls == []


class TestDecideTable:
    def test_client_task_writes_are_not_held(self) -> None:
        assert not decide(WRITE, caller=Caller.CLIENT_TASK).requires_approval

    @pytest.mark.parametrize(
        "tool",
        ["project.create", "flow.propose", "run.start_design_flow", "twin.attempt_promotion"],
    )
    def test_outside_the_task_is_refused(self, tool: str) -> None:
        assert decide(tool, caller=Caller.CLIENT_TASK).refused

    @pytest.mark.parametrize("tool", ["phase.submit", "flow.await_gate"])
    def test_handing_back_and_asking_the_person_are_allowed(self, tool: str) -> None:
        result = decide(tool, caller=Caller.CLIENT_TASK)
        assert not result.refused and not result.requires_approval

    def test_the_worker_takes_no_phase_tasks(self) -> None:
        assert decide("phase.claim", caller=Caller.SERVICE).refused


# ── the verifier ─────────────────────────────────────────────────────────


class TestVerifier:
    async def test_a_claimed_task_on_a_running_client_run_is_confirmed(
        self, gateway_http: Any
    ) -> None:
        run_id, task_id = _client_run()
        task_routes.get_client_task_store().claim(task_id, "codex")
        binding = TaskBinding(
            task_id=task_id, run_id=run_id, phase="needs", project_id=uuid.UUID(PROJECT)
        )
        await _verifier(gateway_http).confirm(binding)

    async def test_an_open_task_is_not_a_claim(self, gateway_http: Any) -> None:
        run_id, task_id = _client_run()
        binding = TaskBinding(
            task_id=task_id, run_id=run_id, phase="needs", project_id=uuid.UUID(PROJECT)
        )
        with pytest.raises(ClientTaskNotConfirmedError, match="not claimed"):
            await _verifier(gateway_http).confirm(binding)

    async def test_a_server_mode_run_is_never_confirmed(self, gateway_http: Any) -> None:
        run_id, task_id = _client_run()
        task_routes.get_client_task_store().claim(task_id, "codex")
        run_routes.get_run_store().get(run_id).request["intelligence"] = "server"
        binding = TaskBinding(
            task_id=task_id, run_id=run_id, phase="needs", project_id=uuid.UUID(PROJECT)
        )
        with pytest.raises(ClientTaskNotConfirmedError, match="client-mode"):
            await _verifier(gateway_http).confirm(binding)

    async def test_another_project_is_never_confirmed(self, gateway_http: Any) -> None:
        run_id, task_id = _client_run()
        task_routes.get_client_task_store().claim(task_id, "codex")
        binding = TaskBinding(
            task_id=task_id, run_id=run_id, phase="needs", project_id=uuid.UUID(OTHER_PROJECT)
        )
        with pytest.raises(ClientTaskNotConfirmedError, match="project"):
            await _verifier(gateway_http).confirm(binding)


# ── the switch and the binding ───────────────────────────────────────────


def test_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(CLIENT_TASK_WRITES_ENV, raising=False)
    monkeypatch.setenv("METAFORGE_GATEWAY_URL", "http://gw")
    assert _client_task_verifier_from_env() is None


def test_on_without_a_gateway_stays_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CLIENT_TASK_WRITES_ENV, "on")
    monkeypatch.delenv("METAFORGE_GATEWAY_URL", raising=False)
    assert _client_task_verifier_from_env() is None


def test_on_with_a_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CLIENT_TASK_WRITES_ENV, "true")
    monkeypatch.setenv("METAFORGE_GATEWAY_URL", "http://gw")
    assert isinstance(_client_task_verifier_from_env(), GatewayClientTaskVerifier)


def test_a_session_without_a_stable_id_binds_nothing() -> None:
    with with_context(McpCallContext()):
        assert bind_current_session_task("t", "r", "p", PROJECT) is False


def test_clearing_another_task_leaves_the_binding() -> None:
    session = uuid.UUID(SESSION)
    with with_context(McpCallContext(session_id=session)):
        assert bind_current_session_task("t1", "r", "p", PROJECT)
    clear_session_task(session, "t2")
    assert bound_task(session) is not None
    clear_session_task(session, "t1")
    assert bound_task(session) is None
