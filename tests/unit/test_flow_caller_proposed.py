"""Caller-proposed tailoring: flow.propose with operations (FORGE-481).

The caller's own model writes the template choice and operations; the server
applies them with the deterministic generator and makes no generator call.
Four properties are protected:

* **no generator call.** A fake model that fails if invoked is installed, and
  the held proposal still comes back, recording the caller as its author.
* **invalid operations are refused** with the reason, and nothing is stored.
* **invariants still run.** FORGE-463's physical-verification rule refuses a
  caller that drops simulation with unknown loads.
* **required questions still come from the server.**
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

_SHELF = "A wall shelf I can make with what I have."

_FULL: dict[str, Any] = {
    "manufacturingContext": {
        "route": "in_house",
        "processes": ["woodworking"],
        "machines": ["table saw, 600 mm rip capacity"],
        "stockMaterials": ["18 mm birch plywood"],
    },
    "targetMaturity": "physically_validated",
    "loadsAndUse": "20 kg of books, indoors",
}

_DROP_SIM = {"op": "drop_phase", "phase": "simulation", "rationale": "load known, test instead"}
_TEST_PLAN = {
    "op": "add_deliverable",
    "phase": "design",
    "value": "test_plan",
    "rationale": "a 20 kg load test on the built shelf",
}
_CALLER = {"client": "claude-code", "model": "claude-sonnet-5-5"}


@pytest.fixture(scope="module")
def client() -> TestClient:
    from api_gateway.server import create_app

    return TestClient(create_app())


@pytest.fixture(autouse=True)
def no_model_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Any server-side model call fails the test."""
    import api_gateway.chat.harness_backend as backend
    import api_gateway.design_flows.generate as gen

    calls: list[str] = []

    async def boom(*args: Any, **kwargs: Any) -> Any:
        calls.append("called")
        raise AssertionError("the server made a generator model call")

    monkeypatch.setattr(backend, "run_chat_turn", boom)
    monkeypatch.setattr(gen, "generate_proposal", boom)
    monkeypatch.setattr(gen, "suggest_extra_questions", boom)
    return calls


def _propose(client: TestClient, **extra: Any) -> Any:
    return client.post("/v1/design-flows/propose", json={"intent": _SHELF, **_FULL, **extra})


def test_shelf_tailoring_yields_a_held_proposal_with_zero_generator_calls(
    client: TestClient, no_model_calls: list[str]
) -> None:
    response = _propose(
        client, template="mech_v1", operations=[_DROP_SIM, _TEST_PLAN], caller=_CALLER
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "proposed"
    assert body["approvalId"]
    assert body["valid"] is True
    assert body["baseTemplateId"] == "mech_v1"
    assert "simulation" not in [p["id"] for p in body["flow"]["phases"]]
    assert [c["op"] for c in body["changes"]] == ["drop_phase", "add_deliverable"]
    assert body["generatedBy"] is None
    assert body["proposedBy"] == {"proposed_by": "caller", **_CALLER}
    assert no_model_calls == []


def test_the_approval_and_version_record_the_caller(client: TestClient) -> None:
    from api_gateway.chat.tool_approvals import get_approval_store
    from orchestrator.design_flow.versions import get_version_store

    body = _propose(client, template="mech_v1", operations=[_TEST_PLAN], caller=_CALLER).json()

    held = get_approval_store().get(body["approvalId"])
    assert held is not None
    assert held.request["proposed_by"] == {"proposed_by": "caller", **_CALLER}
    assert held.request["generated_by"] is None
    version = get_version_store().get(body["versionId"])
    assert version.origin == "caller"


def test_generator_usage_is_zero(client: TestClient) -> None:
    from orchestrator.harness.providers.usage import get_usage_store

    store = get_usage_store()
    before = len(store.list()) if hasattr(store, "list") else None
    assert _propose(client, template="mech_v1", operations=[_TEST_PLAN]).status_code == 201
    if before is not None:
        assert len(store.list()) == before


def test_an_unknown_operation_is_refused_with_the_reason(client: TestClient) -> None:
    from orchestrator.design_flow.versions import get_version_store

    before = len(get_version_store().list())
    response = _propose(
        client,
        template="mech_v1",
        operations=[{"op": "remove_gate", "phase": "design", "rationale": "faster"}],
    )
    assert response.status_code == 422
    assert "unknown operation 'remove_gate'" in response.json()["detail"]
    assert len(get_version_store().list()) == before


def test_a_server_only_operation_is_refused(client: TestClient) -> None:
    response = _propose(
        client,
        template="mech_v1",
        operations=[{"op": "add_route_selection", "phase": "design", "rationale": "x"}],
    )
    assert response.status_code == 422
    assert "unknown operation" in response.json()["detail"]


def test_an_unknown_phase_is_refused(client: TestClient) -> None:
    response = _propose(
        client,
        template="mech_v1",
        operations=[{"op": "drop_phase", "phase": "firmware", "rationale": "none"}],
    )
    assert response.status_code == 422
    assert "unknown phase 'firmware'" in response.json()["detail"]


def test_an_unknown_template_is_refused(client: TestClient) -> None:
    response = _propose(client, template="nope_v9", operations=[])
    assert response.status_code == 422
    assert "unknown template 'nope_v9'" in response.json()["detail"]


def test_an_operation_without_a_rationale_is_refused(client: TestClient) -> None:
    response = _propose(
        client, template="mech_v1", operations=[{"op": "drop_phase", "phase": "simulation"}]
    )
    assert response.status_code == 422
    assert "rationale is required" in response.json()["detail"]


def test_an_invariant_violation_is_refused_and_nothing_is_stored(client: TestClient) -> None:
    from orchestrator.design_flow.versions import get_version_store

    before = len(get_version_store().list())
    response = _propose(client, template="mech_v1", operations=[_DROP_SIM], loadsAndUse="unknown")
    assert response.status_code == 422
    assert "physical-verification-kept" in response.json()["detail"]
    assert len(get_version_store().list()) == before


def test_missing_context_still_returns_needs_input_with_operations(client: TestClient) -> None:
    response = client.post(
        "/v1/design-flows/propose",
        json={"intent": _SHELF, "template": "mech_v1", "operations": [_TEST_PLAN]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "needs_input"
    assert [q["id"] for q in body["questions"]] == [
        "manufacturing_route",
        "target_maturity",
        "loads_and_use",
    ]
    assert all(q["source"] == "metaforge" for q in body["questions"])


async def test_the_mcp_tool_carries_the_operations_to_the_route() -> None:
    from api_gateway.design_flows.mcp_bindings import make_proposer

    result = await make_proposer()(
        intent=_SHELF,
        project_id=None,
        requirements=[],
        manufacturing_context=_FULL["manufacturingContext"],
        target_maturity=_FULL["targetMaturity"],
        loads_and_use=_FULL["loadsAndUse"],
        template="mech_v1",
        operations=[_DROP_SIM, _TEST_PLAN],
        caller=_CALLER,
    )
    assert result["status"] == "proposed"
    assert result["proposed_by"]["client"] == "claude-code"
    assert [c["op"] for c in result["changes"]] == ["drop_phase", "add_deliverable"]


async def test_an_mcp_refusal_carries_the_reason() -> None:
    from api_gateway.design_flows.mcp_bindings import make_proposer

    with pytest.raises(RuntimeError, match="unknown phase 'firmware'"):
        await make_proposer()(
            intent=_SHELF,
            project_id=None,
            requirements=[],
            manufacturing_context=_FULL["manufacturingContext"],
            target_maturity=_FULL["targetMaturity"],
            loads_and_use=_FULL["loadsAndUse"],
            template="mech_v1",
            operations=[{"op": "drop_phase", "phase": "firmware", "rationale": "none"}],
        )


def test_the_adapter_schema_advertises_operations() -> None:
    from tool_registry.tools.design_flow.adapter import DesignFlowServer

    async def proposer(**kwargs: Any) -> dict[str, Any]:
        return {}

    server = DesignFlowServer(proposer=proposer)
    props = server._tools["flow.propose"].manifest.input_schema["properties"]
    assert {"template", "operations", "caller"} <= set(props)
