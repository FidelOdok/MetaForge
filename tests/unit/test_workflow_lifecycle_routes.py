"""The lifecycle surface over HTTP (FORGE-539).

Intent compilation, capability coverage, graph fields in the catalogue, the
intent and capabilities carried on a proposal, and a run's lifecycle view
with its completion verdict.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api_gateway.design_flows.lifecycle_service as service
import api_gateway.runs.routes as run_routes
from orchestrator.design_flow.versions import reset_version_store

_SHELF = "A wall shelf that holds at least 20 kg with deflection under 3 mm"
_FULL: dict[str, Any] = {
    "manufacturingContext": {
        "route": "in_house",
        "processes": ["woodworking"],
        "stockMaterials": ["18 mm birch plywood"],
    },
    "targetMaturity": "concept",
    "loadsAndUse": "20 kg of books, indoors",
}


@dataclass
class _Manifest:
    tool_id: str


@dataclass
class _Health:
    status: str


class _FakeRegistry:
    """A registry with chosen tools, and one adapter that is down."""

    def __init__(self, tools: list[str], down: set[str] | None = None) -> None:
        self._tools = tools
        self._down = down or set()

    def list_tools(self) -> list[_Manifest]:
        return [_Manifest(t) for t in self._tools]

    async def check_all_health(self) -> dict[str, _Health]:
        adapters = {t.split(".", 1)[0] for t in self._tools}
        return {a: _Health("unhealthy" if a in self._down else "healthy") for a in adapters}

    def get_adapter_for_tool(self, tool_id: str) -> str:
        return tool_id.split(".", 1)[0]


@pytest.fixture(autouse=True)
def _no_registry() -> Any:
    before = service._tool_registry
    service.set_tool_registry(None)
    yield
    service.set_tool_registry(before)


@pytest.fixture(scope="module")
def client() -> TestClient:
    from api_gateway.server import create_app

    return TestClient(create_app())


class TestCatalogue:
    def test_flows_carry_their_graph(self, client: TestClient) -> None:
        body = client.get("/v1/design-flows").json()
        flow = next(f for f in body["flows"] if f["id"] == "mech_v1")
        assert flow["graph"]["linear"] is True
        assert flow["graph"]["waves"][0] == ["intent"]
        phase = flow["phases"][0]
        assert phase["dependsOn"] is None and phase["condition"] is None


class TestIntent:
    def test_compile(self, client: TestClient) -> None:
        response = client.post("/v1/design-flows/intent", json={"intent": _SHELF, **_FULL})
        assert response.status_code == 200, response.text
        intent = response.json()["intent"]
        assert intent["primary_goal"]["type"] == "design"
        criteria = {(c["operator"], c["limit"], c["unit"]) for c in intent["success_criteria"]}
        assert (">=", 20.0, "kg") in criteria and ("<=", 3.0, "mm") in criteria
        assert response.json()["missingInputs"] == []

    def test_missing_inputs_are_blocking_unknowns(self, client: TestClient) -> None:
        response = client.post("/v1/design-flows/intent", json={"intent": _SHELF})
        body = response.json()
        assert body["missingInputs"], "route, maturity and loads were not given"
        assert body["intent"]["ready_to_plan"] is False

    def test_empty_intent_is_refused(self, client: TestClient) -> None:
        assert client.post("/v1/design-flows/intent", json={"intent": " "}).status_code == 400


class TestCapabilities:
    def test_no_registry_is_a_limit_not_a_finding(self, client: TestClient) -> None:
        report = client.get("/v1/design-flows/mech_v1/capabilities").json()["report"]
        assert report["gaps"] == []
        assert "not a finding" in report["limits"][0]

    def test_a_down_solver_is_reported(self, client: TestClient) -> None:
        from mcp_core.profiles import DELIVERABLE_TOOLS

        every_tool = sorted({t for tools in DELIVERABLE_TOOLS.values() for t in tools})
        service.set_tool_registry(_FakeRegistry(every_tool, down={"calculix"}))
        report = client.get("/v1/design-flows/mech_v1/capabilities").json()["report"]
        gap = next(g for g in report["gaps"] if g["capability"] == "simulation_result")
        assert gap["severity"] == "REQUIRES_USER_ACTION"
        assert "calculix.run_fea" in gap["unreachable_tools"]
        # mech_v1's simulation phase *expects* a simulation_result but its gate
        # requires only a design_decision, so the down solver degrades the
        # flow rather than blocking it.
        assert gap["blocking"] is False
        assert report["status"] == "READY_WITH_WARNINGS"

    def test_a_profile_narrows_coverage(self, client: TestClient) -> None:
        from mcp_core.profiles import DELIVERABLE_TOOLS

        every_tool = sorted({t for tools in DELIVERABLE_TOOLS.values() for t in tools})
        service.set_tool_registry(_FakeRegistry(every_tool))
        body = client.get("/v1/design-flows/mech_v1/capabilities?profile=core").json()
        assert body["profile"] == "core"
        assert any(g["unserved_tools"] for g in body["report"]["gaps"])

    def test_unknown_profile_and_template(self, client: TestClient) -> None:
        assert client.get("/v1/design-flows/mech_v1/capabilities?profile=nope").status_code == 400
        assert client.get("/v1/design-flows/nope/capabilities").status_code == 404


class TestProposalCarriesIntentAndCapabilities:
    def test_caller_proposal(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        import api_gateway.design_flows.generate as gen

        async def boom(*_: Any, **__: Any) -> Any:
            raise AssertionError("no model call expected")

        monkeypatch.setattr(gen, "generate_proposal", boom)
        response = client.post(
            "/v1/design-flows/propose",
            json={
                "intent": _SHELF,
                **_FULL,
                "template": "mech_v1",
                "operations": [],
                "caller": {"client": "test", "model": "none"},
            },
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["intentModel"]["primary_goal"]["type"] == "design"
        assert body["capabilities"]["status"] in {"READY", "READY_WITH_WARNINGS", "BLOCKED"}
        assert body["flow"]["graph"]["linear"] is True


class TestRunLifecycle:
    @pytest.fixture(autouse=True)
    def _fresh(self) -> Any:
        run_routes.reset_run_store()
        reset_version_store()
        yield
        run_routes.reset_run_store()
        reset_version_store()

    @pytest.fixture
    def gateway(self) -> httpx.AsyncClient:
        app = FastAPI()
        app.include_router(run_routes.router)
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw")

    def _run(self, *, complete: bool = True, fail: bool = False) -> str:
        store = run_routes._store
        run = store.create({"kind": "design_flow", "flow": "mech_v1", "flow_engine": "in_process"})
        store.start(run.id)
        if fail:
            store.fail(run.id, "Phase 'design' failed: boom")
        elif complete:
            from orchestrator.design_flow.spec import get_flow

            store.complete(
                run.id,
                result={
                    "phases": [
                        {"id": p.id, "status": "completed"} for p in get_flow("mech_v1").phases
                    ],
                    "skipped": [],
                },
            )
        return run.id

    @pytest.mark.asyncio
    async def test_a_completed_run_without_requirements_is_not_verified(
        self, gateway: httpx.AsyncClient
    ) -> None:
        run_id = self._run()
        response = await gateway.get(f"/v1/runs/{run_id}/lifecycle")
        assert response.status_code == 200, response.text
        body = response.json()
        completion = body["lifecycle"]["completion"]
        assert completion["classification"] == "COMPLETED_WITH_WARNINGS"
        assert completion["verified"] is False
        assert any("no project" in limit for limit in body["limits"])
        nodes = {n["id"]: n for n in body["lifecycle"]["nodes"]}
        assert nodes["design"]["execution_status"] == "SUCCEEDED"
        assert nodes["design"]["objective_status"] == "SATISFIED"

    @pytest.mark.asyncio
    async def test_a_failed_run_is_failed(self, gateway: httpx.AsyncClient) -> None:
        run_id = self._run(fail=True)
        body = (await gateway.get(f"/v1/runs/{run_id}/lifecycle")).json()
        assert body["lifecycle"]["completion"]["classification"] == "FAILED"

    @pytest.mark.asyncio
    async def test_unknown_and_non_flow_runs(self, gateway: httpx.AsyncClient) -> None:
        assert (await gateway.get("/v1/runs/nope/lifecycle")).status_code == 404
        run = run_routes._store.create({"kind": "chat"})
        assert (await gateway.get(f"/v1/runs/{run.id}/lifecycle")).status_code == 422
