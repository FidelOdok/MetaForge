"""An agent can see who decided a gate, however it was decided (FORGE-695).

Found in the plugin end-to-end run on the sandbox: gates approved through
``POST /v1/approvals/{id}/decision`` (an agent acting for a person) read to the
waiting plugin agent as "closed without anyone approving it". The run keeps
every decision in ``request["decisions"]``; these tests check it reaches the
agent through the sidecar's bindings (what a plugin talks to) and through the
gateway's own run status.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import httpx

from metaforge.mcp import remote_flows
from orchestrator.harness.runs import RunStatus

GATEWAY = "http://gateway.test"

DECISION: dict[str, Any] = {
    "decision": "approve",
    "reason": "Plugin e2e test: gate clean.",
    "approver": "Fidel Odok",
    "approver_verified": False,
    "surface": "agent",
    "on_behalf_of": "Fidel Odok",
    "agent": "claude-code",
    "decided_at": "2026-10-26T16:57:53+00:00",
}


def _run_json(status: str, decisions: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": "run-1",
        "status": status,
        "request": {"flow": "hardware_v1", "goal": "a board", "decisions": decisions},
    }


def _bindings(run: dict[str, Any]) -> remote_flows.RemoteFlowBindings:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/runs/run-1", request.url.path
        return httpx.Response(200, json=run)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=GATEWAY)
    return remote_flows.build_remote_flow_bindings(GATEWAY, client=client)


def test_the_sidecar_gate_reader_carries_the_last_decision() -> None:
    bindings = _bindings(_run_json("running", [{**DECISION, "decision": "retry"}, DECISION]))
    state = asyncio.run(bindings.gate_reader("run-1"))
    assert state["gate"] is None
    assert state["last_decision"] == DECISION


def test_the_sidecar_run_status_names_who_decided() -> None:
    bindings = _bindings(_run_json("running", [DECISION]))
    out = asyncio.run(bindings.run_launcher.status(run_id="run-1"))
    assert out["last_gate_decision"]["surface"] == "agent"
    assert out["last_gate_decision"]["on_behalf_of"] == "Fidel Odok"


def test_a_run_with_no_decision_says_nothing_about_one() -> None:
    bindings = _bindings(_run_json("running", []))
    out = asyncio.run(bindings.run_launcher.status(run_id="run-1"))
    assert "last_gate_decision" not in out
    assert asyncio.run(bindings.gate_reader("run-1"))["last_decision"] is None


def test_the_gateway_run_status_names_who_decided(monkeypatch: Any) -> None:
    from api_gateway.runs import routes
    from api_gateway.runs.launcher import RunLauncher

    run = SimpleNamespace(
        id="run-1",
        status=RunStatus.RUNNING,
        request={"flow": "hardware_v1", "goal": "a board", "decisions": [DECISION]},
        approval_reason=None,
        error=None,
        result=None,
    )
    monkeypatch.setattr(routes, "_store", SimpleNamespace(get=lambda _id: run))
    out = asyncio.run(RunLauncher().status(run_id="run-1"))
    assert out["last_gate_decision"] == DECISION
