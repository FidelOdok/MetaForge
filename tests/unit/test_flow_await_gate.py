"""A design-flow gate decided by the person in the client's chat (FORGE-582)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from mcp_core.annotations import annotations_for
from mcp_core.elicitation import ElicitAction, ElicitResult, with_elicitor
from mcp_core.guardrails import Caller, decide
from tool_registry.tools.design_flow.adapter import DesignFlowServer
from tool_registry.tools.design_flow.gates import await_gate, gate_question


def _gate(**over: Any) -> dict[str, Any]:
    gate = {
        "id": "gate:run-1",
        "status": "pending",
        "title": "table gate at phase 'needs'",
        "summary": "needs_review: 2 needs recorded",
        "findings": [],
        "allowed_decisions": ["approve", "reject", "retry", "rework"],
        "rework_targets": ["intent"],
        "reason_required_for": ["reject", "retry", "rework"],
        "decidable": True,
        "detail": {"retries_left": 2},
    }
    gate.update(over)
    return gate


class _Gateway:
    """Reader and decider over a scripted run."""

    def __init__(self, states: list[dict[str, Any]], refuse: str | None = None) -> None:
        self.states = states
        self.decisions: list[tuple[Any, ...]] = []
        self.refuse = refuse

    async def read(self, run_id: str) -> dict[str, Any]:
        return self.states.pop(0) if len(self.states) > 1 else self.states[0]

    async def decide(self, *args: Any) -> dict[str, Any]:
        if self.refuse:
            raise RuntimeError(self.refuse)
        self.decisions.append(args)
        return {"status": "approved", "decision": {"approver": args[4]}}


class _Person:
    """An elicitor: what the person answers in the client's prompt."""

    def __init__(self, result: ElicitResult) -> None:
        self.result = result
        self.asked: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, message: str, schema: dict[str, Any]) -> ElicitResult:
        self.asked.append((message, schema))
        return self.result


async def _no_sleep(_: float) -> None:
    return None


def _run(gw: _Gateway, person: _Person | None, **kw: Any) -> dict[str, Any]:
    async def go() -> dict[str, Any]:
        with with_elicitor(person):
            return await await_gate(
                "run-1", reader=gw.read, decider=gw.decide, sleep=_no_sleep, **kw
            )

    return asyncio.run(go())


def test_the_person_approves_in_chat() -> None:
    gw = _Gateway([{"run_status": "running"}, {"run_status": "awaiting_approval", "gate": _gate()}])
    person = _Person(ElicitResult(ElicitAction.ACCEPT, {"decision": "approve"}))
    out = _run(gw, person)
    assert out["status"] == "decided"
    assert out["decision"] == "approve"
    assert gw.decisions[0][:2] == ("gate:run-1", "approve")
    message, schema = person.asked[0]
    assert "needs_review" in message
    assert schema["properties"]["decision"]["enum"] == ["approve", "reject", "retry", "rework"]
    assert schema["properties"]["to_phase"]["enum"] == ["intent"]


def test_the_approver_is_the_session_not_the_answer() -> None:
    gw = _Gateway([{"run_status": "awaiting_approval", "gate": _gate()}])
    person = _Person(
        ElicitResult(ElicitAction.ACCEPT, {"decision": "approve", "approver": "someone-else"})
    )
    _run(gw, person)
    # Unauthenticated session: the honest unverified stand-in, never a name from the form.
    assert gw.decisions[0][4] == "local:elicitation"
    assert gw.decisions[0][5] is False


def test_a_gate_that_is_not_ready_never_offers_approve() -> None:
    gate = _gate(
        allowed_decisions=["reject", "retry"],
        findings=[{"severity": "error", "message": "missing stakeholder_need"}],
    )
    message, schema = gate_question(gate, 300)
    assert schema["properties"]["decision"]["enum"] == ["reject", "retry"]
    assert "to_phase" not in schema["properties"]
    assert "Approve is not offered" in message
    assert "missing stakeholder_need" in message


def test_an_answer_outside_the_allowed_set_changes_nothing() -> None:
    gate = _gate(allowed_decisions=["reject"])
    gw = _Gateway([{"run_status": "awaiting_approval", "gate": gate}])
    out = _run(gw, _Person(ElicitResult(ElicitAction.ACCEPT, {"decision": "approve"})))
    assert out["status"] == "awaiting_gate"
    assert gw.decisions == []


def test_a_reason_is_required_where_the_gate_requires_one() -> None:
    gw = _Gateway([{"run_status": "awaiting_approval", "gate": _gate()}])
    out = _run(gw, _Person(ElicitResult(ElicitAction.ACCEPT, {"decision": "reject"})))
    assert out["status"] == "awaiting_gate"
    assert gw.decisions == []


@pytest.mark.parametrize("action", [ElicitAction.CANCEL, ElicitAction.DECLINE])
def test_dismissing_or_declining_leaves_the_gate_open(action: ElicitAction) -> None:
    gw = _Gateway([{"run_status": "awaiting_approval", "gate": _gate()}])
    out = _run(gw, _Person(ElicitResult(action)))
    assert out["status"] == "awaiting_gate"
    assert "dashboard" in out["message"]
    assert gw.decisions == []


def test_a_client_that_cannot_ask_points_at_the_dashboard() -> None:
    gw = _Gateway([{"run_status": "awaiting_approval", "gate": _gate()}])
    out = _run(gw, None)
    assert out["status"] == "awaiting_gate"
    assert "cannot decide a gate yourself" in out["message"]
    assert gw.decisions == []


def test_an_expired_prompt_is_not_a_decision() -> None:
    class _Slow:
        async def __call__(self, message: str, schema: dict[str, Any]) -> ElicitResult:
            await asyncio.sleep(5)
            return ElicitResult(ElicitAction.ACCEPT, {"decision": "approve"})

    gw = _Gateway([{"run_status": "awaiting_approval", "gate": _gate()}])

    async def go() -> dict[str, Any]:
        with with_elicitor(_Slow()):
            return await await_gate("run-1", reader=gw.read, decider=gw.decide, answer_seconds=0.05)

    # answer_seconds is floored at 1s, so this waits about a second.
    out = asyncio.run(go())
    assert out["status"] == "awaiting_gate"
    assert gw.decisions == []


def test_the_gateway_refusal_is_reported_and_the_gate_stays_open() -> None:
    gw = _Gateway(
        [{"run_status": "awaiting_approval", "gate": _gate()}],
        refuse="gate not ready: retry the phase or reject",
    )
    out = _run(gw, _Person(ElicitResult(ElicitAction.ACCEPT, {"decision": "approve"})))
    assert out["status"] == "awaiting_gate"
    assert "gate not ready" in out["message"]


def test_a_finished_run_has_no_gate() -> None:
    out = _run(_Gateway([{"run_status": "completed"}]), None)
    assert out["status"] == "run_ended"


def test_no_gate_within_the_wait_hands_back() -> None:
    clock = iter([0.0, 0.0, 5.0, 500.0, 500.0])
    gw = _Gateway([{"run_status": "running"}])

    async def go() -> dict[str, Any]:
        return await await_gate(
            "run-1",
            reader=gw.read,
            decider=gw.decide,
            wait_seconds=10,
            sleep=_no_sleep,
            clock=lambda: next(clock),
        )

    out = asyncio.run(go())
    assert out["status"] == "no_gate_yet"


def test_an_undecidable_gate_is_not_put_to_the_person() -> None:
    gate = _gate(decidable=False, not_decidable_reason="the run's workflow no longer exists")
    person = _Person(ElicitResult(ElicitAction.ACCEPT, {"decision": "approve"}))
    out = _run(_Gateway([{"run_status": "awaiting_approval", "gate": gate}]), person)
    assert out["status"] == "awaiting_gate"
    assert person.asked == []


# ── classification and registration ──────────────────────────────────────


def test_await_gate_is_not_held_before_it_asks() -> None:
    # Holding the call would ask the same person twice for one decision.
    assert not decide("flow.await_gate", caller=Caller.UNTRUSTED).requires_approval
    assert annotations_for("flow.await_gate")["destructiveHint"] is False


def test_phase_tools_are_classified() -> None:
    assert annotations_for("phase.list_tasks")["readOnlyHint"] is True
    for tool in ("phase.claim", "phase.submit"):
        hints = annotations_for(tool)
        assert hints["readOnlyHint"] is False
        assert hints["destructiveHint"] is False


def test_the_worker_may_not_ask_for_gate_decisions_or_claim_tasks() -> None:
    # The design-flow service caller already may not touch flow.* tools.
    assert decide("flow.await_gate", caller=Caller.SERVICE).refused


async def _noop(*_: Any, **__: Any) -> dict[str, Any]:
    return {}


def test_tools_register_only_with_their_bindings() -> None:
    bare = DesignFlowServer(run_status_reader=_noop)
    assert "flow.await_gate" not in bare.tool_ids
    assert "phase.claim" not in bare.tool_ids

    class _Tasks:
        list_tasks = claim = submit = staticmethod(_noop)

    full = DesignFlowServer(gate_reader=_noop, gate_decider=_noop, client_tasks=_Tasks())
    assert {"flow.await_gate", "phase.list_tasks", "phase.claim", "phase.submit"} <= set(
        full.tool_ids
    )


def test_start_run_passes_the_mode_only_when_given() -> None:
    seen: list[dict[str, Any]] = []

    async def starter(**kwargs: Any) -> dict[str, Any]:
        seen.append(kwargs)
        return {"run_id": "r"}

    server = DesignFlowServer(run_starter=starter)
    asyncio.run(server.start_run({"flow_version_id": "v", "goal": "g"}))
    asyncio.run(server.start_run({"flow_version_id": "v", "goal": "g", "intelligence": "client"}))
    assert "intelligence" not in seen[0]
    assert seen[1]["intelligence"] == "client"
