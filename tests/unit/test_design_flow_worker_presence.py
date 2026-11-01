"""Design-flow runs need a worker, and say so when they do not have one (FORGE-475).

A run accepted while nothing polls ``metaforge-design-flows`` sat queued for
ever and every state query against it timed out. These cover the three halves
of the fix: the gateway refuses such a run, the live view of a run it cannot
query shows the run's own frozen version, and a phase that fails on provider
configuration ends the run with the error.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

import api_gateway.runs.routes as routes
from orchestrator.design_flow.generator import Operation, OperationKind, build_proposal
from orchestrator.design_flow.launcher import (
    DesignFlowLauncher,
    DesignFlowWorkerUnavailableError,
)
from orchestrator.design_flow.spec import get_flow
from orchestrator.design_flow.templates import load_templates
from orchestrator.design_flow.versions import get_version_store, reset_version_store
from orchestrator.design_flow.worker_presence import WorkerPresence


class _Client:
    """A Temporal client double whose task queue has the given pollers."""

    def __init__(self, identities: list[str]) -> None:
        self.identities = identities
        self.describe_calls = 0
        self.started: list[Any] = []
        self.workflow_service = SimpleNamespace(describe_task_queue=self._describe)

    async def _describe(self, request: Any) -> Any:
        self.describe_calls += 1
        return SimpleNamespace(pollers=[SimpleNamespace(identity=i) for i in self.identities])

    async def start_workflow(self, *args: Any, **kwargs: Any) -> None:
        self.started.append((args, kwargs))

    def get_workflow_handle(self, workflow_id: str) -> Any:
        raise RuntimeError("Timeout expired")


@pytest.fixture(autouse=True)
def _fresh(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("METAFORGE_FLOW_ENGINE", "temporal")
    routes.reset_run_store()
    reset_version_store()
    yield
    routes.set_flow_launcher(None)
    routes.reset_run_store()
    reset_version_store()


@pytest.fixture
def gateway() -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(routes.router)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw.test")


def _approved_version() -> Any:
    proposal = build_proposal(
        get_flow("hardware_v1"),
        base_version=load_templates()["hardware_v1"].version,
        operations=[Operation(OperationKind.DROP_PHASE, "firmware", "no firmware here")],
        intent="a passive enclosure",
    )
    assert proposal.validation.ok, proposal.validation
    store = get_version_store()
    version = store.save(
        proposal.definition,
        base_template_id="hardware_v1",
        base_version=proposal.base_version,
        changes=[],
    )
    return store.decide(version.id, approved=True, decided_by="reviewer")


def _start_body(version_id: str) -> dict[str, Any]:
    return {
        "request": {
            "kind": "design_flow",
            "flow_version_id": version_id,
            "goal": "a passive enclosure",
        },
        "start": True,
    }


class TestRunCreationNeedsAWorker:
    async def test_no_poller_is_a_503_with_the_reason_and_no_run(
        self, gateway: httpx.AsyncClient
    ) -> None:
        client = _Client([])
        routes.set_flow_launcher(DesignFlowLauncher(client=client))
        version = _approved_version()

        response = await gateway.post("/v1/runs", json=_start_body(version.id))

        assert response.status_code == 503, response.text
        assert "no worker is polling 'metaforge-design-flows'" in response.json()["detail"]
        assert client.started == [], "nothing may be handed to Temporal"
        assert (await gateway.get("/v1/runs")).json()["runs"] == []

    async def test_a_polling_worker_lets_the_run_start(self, gateway: httpx.AsyncClient) -> None:
        client = _Client(["1@worker"])
        routes.set_flow_launcher(DesignFlowLauncher(client=client))
        version = _approved_version()

        response = await gateway.post("/v1/runs", json=_start_body(version.id))

        assert response.status_code == 201, response.text
        assert len(client.started) == 1

    async def test_the_answer_is_cached_between_requests(self) -> None:
        client = _Client(["1@worker"])
        launcher = DesignFlowLauncher(client=client)

        for _ in range(5):
            await launcher.require_worker()

        assert client.describe_calls == 1

    async def test_the_cache_expires(self) -> None:
        now = [0.0]
        client = _Client([])

        async def counted() -> list[str]:
            client.describe_calls += 1
            return list(client.identities)

        presence = WorkerPresence(probe=counted, cache_seconds=10, _clock=lambda: now[0])
        assert (await presence.check())[0] is False
        client.identities = ["1@worker"]
        assert (await presence.check())[0] is False, "still inside the cache window"
        now[0] = 11.0
        assert (await presence.check())[0] is True
        assert client.describe_calls == 2

    async def test_a_failing_probe_is_absent_not_present(self) -> None:
        async def boom() -> list[str]:
            raise RuntimeError("temporal went away")

        present, reason, _ = await WorkerPresence(probe=boom).check()

        assert present is False
        assert "temporal went away" in reason

    async def test_require_worker_raises_the_typed_error(self) -> None:
        launcher = DesignFlowLauncher(client=_Client([]))
        with pytest.raises(DesignFlowWorkerUnavailableError) as raised:
            await launcher.require_worker()
        assert raised.value.task_queue == "metaforge-design-flows"


class TestStatusWhenTheStateCannotBeRead:
    async def test_it_lists_the_frozen_versions_phases_as_unknown(
        self, gateway: httpx.AsyncClient
    ) -> None:
        client = _Client(["1@worker"])
        routes.set_flow_launcher(DesignFlowLauncher(client=client))
        version = _approved_version()
        created = (await gateway.post("/v1/runs", json=_start_body(version.id))).json()

        state = (await gateway.get(f"/v1/runs/{created['id']}/flow-state")).json()

        approved = [p.id for p in version.definition.phases]
        template = [p.id for p in get_flow("hardware_v1").phases]
        assert "firmware" in template and "firmware" not in approved
        assert state["live"] is False
        assert [p["id"] for p in state["phases"]] == approved
        assert {p["status"] for p in state["phases"]} == {"unknown"}
        assert state["flowVersionId"] == version.id
        assert state["flowContentHash"] == version.frozen.content_hash
        assert state["flowVersion"] == version.frozen.version

    async def test_an_unreadable_version_lists_no_phases_rather_than_the_template(
        self, gateway: httpx.AsyncClient
    ) -> None:
        routes.set_flow_launcher(DesignFlowLauncher(client=_Client(["1@worker"])))
        version = _approved_version()
        created = (await gateway.post("/v1/runs", json=_start_body(version.id))).json()
        reset_version_store()

        state = (await gateway.get(f"/v1/runs/{created['id']}/flow-state")).json()

        assert state["phases"] == []
        assert version.id in state["detail"]
        assert state["flowContentHash"] == version.frozen.content_hash


class TestAFailedPhaseIsVisible:
    async def test_the_live_view_marks_the_phase_failed_with_the_error(
        self, gateway: httpx.AsyncClient
    ) -> None:
        version = _approved_version()
        first = version.definition.phases[0].id
        error = "Phase 'x' failed: no usable model provider for this phase: missing API key"

        class _Launcher(DesignFlowLauncher):
            async def state(self, run_id: str) -> dict[str, Any]:
                return {
                    "status": "failed",
                    "current_phase": first,
                    "awaiting_gate": None,
                    "completed": [],
                    "error": error,
                }

            async def events(self, run_id: str) -> list[dict[str, Any]]:
                return [{"event": "run_failed", "detail": error}]

        routes.set_flow_launcher(_Launcher(client=_Client(["1@worker"])))
        created = (await gateway.post("/v1/runs", json=_start_body(version.id))).json()

        state = (await gateway.get(f"/v1/runs/{created['id']}/flow-state")).json()

        assert state["status"] == "failed"
        assert state["error"] == error
        by_id = {p["id"]: p for p in state["phases"]}
        assert by_id[first]["status"] == "failed"
        assert by_id[first]["summary"] == error
