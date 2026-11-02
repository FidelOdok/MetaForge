"""Pending approvals in the client's chat (FORGE-722).

On the sandbox a server-run design flow piled up gates and held writes that
only the dashboard showed; the Claude Code plugin could not see them, and
flow.await_gate only covers one run's gate. approvals.list shows everything
pending; approvals.decide asks the PERSON in the client's prompt and records
their answer through the same decider as a gate.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from mcp_core.elicitation import ElicitAction, ElicitResult, with_elicitor
from mcp_core.guardrails import DOWNSTREAM_APPROVED, SERVICE_REFUSED_PREFIXES
from mcp_core.profiles import tools_for_profile
from tool_registry.tools.design_flow.adapter import DesignFlowServer
from tool_registry.tools.design_flow.approvals import (
    approval_question,
    decide_approval,
    list_pending,
)

GATE = {
    "id": "gate:run_1",
    "kind": "gate",
    "status": "pending",
    "title": "mech_v1 gate at phase 'design'",
    "project_id": "p1",
    "decidable": True,
    "allowed_decisions": ["reject", "retry", "rework"],
    "reason_required_for": ["reject", "retry", "rework"],
    "rework_targets": ["requirements", "design"],
    "findings": [{"severity": "error", "message": "Chair Seat records no material"}],
}
HELD_WRITE = {
    "id": "tool:run_9",
    "kind": "tool_call",
    "status": "pending",
    "title": "Tool call: twin.record_decision",
    "project_id": "p2",
    "decidable": True,
    "allowed_decisions": ["approve", "reject"],
    "reason_required_for": ["reject"],
}


class _Gateway:
    def __init__(self, items: list[dict[str, Any]]) -> None:
        self.items = items
        self.decided: list[tuple[Any, ...]] = []

    async def lister(self, project_id: str | None) -> list[dict[str, Any]]:
        return [i for i in self.items if project_id in (None, i["project_id"])]

    async def decider(self, *args: Any) -> dict[str, Any]:
        self.decided.append(args)
        return {"status": "approved", "decision": {"approver": "local:elicitation"}}


class _Person:
    def __init__(self, result: ElicitResult) -> None:
        self.result = result
        self.asked: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, message: str, schema: dict[str, Any]) -> ElicitResult:
        self.asked.append((message, schema))
        return self.result


def _decide(gw: _Gateway, person: _Person | None, approval_id: str) -> dict[str, Any]:
    async def go() -> dict[str, Any]:
        coro = decide_approval(lister=gw.lister, decider=gw.decider, approval_id=approval_id)
        if person is None:
            return await coro
        with with_elicitor(person):
            return await coro

    return asyncio.run(go())


def test_list_shows_every_kind_and_narrows_by_project() -> None:
    gw = _Gateway([GATE, HELD_WRITE])
    everything = asyncio.run(list_pending(lister=gw.lister))
    assert everything["count"] == 2
    assert {i["kind"] for i in everything["items"]} == {"gate", "tool_call"}
    one = asyncio.run(list_pending(lister=gw.lister, project_id="p2"))
    assert [i["id"] for i in one["items"]] == ["tool:run_9"]


def test_the_person_decides_a_held_write_and_the_decision_is_recorded() -> None:
    gw = _Gateway([HELD_WRITE])
    person = _Person(ElicitResult(ElicitAction.ACCEPT, {"decision": "approve"}))
    out = _decide(gw, person, "tool:run_9")
    assert out["status"] == "decided" and out["decision"] == "approve"
    assert gw.decided[0][:2] == ("tool:run_9", "approve")
    message, schema = person.asked[0]
    assert "twin.record_decision" in message
    assert schema["properties"]["decision"]["enum"] == ["approve", "reject"]


def test_a_gate_offers_only_what_it_allows() -> None:
    message, schema = approval_question(GATE, 80)
    assert schema["properties"]["decision"]["enum"] == ["reject", "retry", "rework"]
    assert schema["properties"]["to_phase"]["enum"] == ["requirements", "design"]
    assert "Approve is not offered" in message
    assert "records no material" in message


def test_a_reason_is_enforced_where_required() -> None:
    gw = _Gateway([GATE])
    person = _Person(ElicitResult(ElicitAction.ACCEPT, {"decision": "retry"}))
    out = _decide(gw, person, "gate:run_1")
    assert out["status"] == "pending" and gw.decided == []


@pytest.mark.parametrize("action", [ElicitAction.CANCEL, ElicitAction.DECLINE])
def test_dismissing_the_prompt_decides_nothing(action: ElicitAction) -> None:
    gw = _Gateway([HELD_WRITE])
    out = _decide(gw, _Person(ElicitResult(action, {})), "tool:run_9")
    assert out["status"] == "pending" and gw.decided == []


def test_a_client_that_cannot_ask_decides_nothing() -> None:
    gw = _Gateway([HELD_WRITE])
    out = _decide(gw, None, "tool:run_9")
    assert out["status"] == "pending" and "cannot show a prompt" in out["message"]
    assert gw.decided == []


def test_an_unknown_or_undecidable_item_is_not_asked() -> None:
    gw = _Gateway([{**HELD_WRITE, "decidable": False, "not_decidable_reason": "it expired"}])
    person = _Person(ElicitResult(ElicitAction.ACCEPT, {"decision": "approve"}))
    assert _decide(gw, person, "tool:nope")["status"] == "pending"
    assert "it expired" in _decide(gw, person, "tool:run_9")["message"]
    assert person.asked == [] and gw.decided == []


def test_the_tools_are_registered_and_guarded() -> None:
    gw = _Gateway([])
    srv = DesignFlowServer(
        gate_reader=gw.lister, gate_decider=gw.decider, approvals_lister=gw.lister
    )
    assert {"approvals.list", "approvals.decide"} <= set(srv.tool_ids)
    # It asks the person itself: holding the call first would ask twice.
    assert "approvals.decide" in DOWNSTREAM_APPROVED
    # The design-flow worker must never decide approvals.
    assert any("approvals.decide".startswith(p) for p in SERVICE_REFUSED_PREFIXES)
    # The plugin's default connection carries both.
    assert {"approvals.list", "approvals.decide"} <= set(tools_for_profile("core"))
