"""Stated loads make the analysis required at the V&V gate (FORGE-570)."""

from __future__ import annotations

from orchestrator.design_flow.context import FlowContext
from orchestrator.design_flow.generator import (
    OperationKind,
    build_proposal,
    parse_caller_operations,
)
from orchestrator.design_flow.spec import get_flow


def _sim(proposal):  # type: ignore[no-untyped-def]
    return next(p for p in proposal.definition.phases if p.id == "simulation")


def _propose(loads: str | None, operations: list | None = None):  # type: ignore[no-untyped-def]
    base = get_flow("mech_v1")
    return build_proposal(
        base,
        base_version="test",
        operations=parse_caller_operations(operations or [], base),
        context=FlowContext(loads_and_use=loads, requirements=("FoS >= 2",)),
    )


def test_stated_loads_require_the_analysis_with_a_recorded_reason() -> None:
    proposal = _propose("98.1 N static on the tip face")
    assert "simulation_result" in _sim(proposal).required_deliverables
    added = [
        op
        for op in proposal.operations
        if op.kind is OperationKind.ADD_DELIVERABLE and op.phase_id == "simulation"
    ]
    assert len(added) == 1 and "loads are stated" in added[0].rationale


def test_unknown_loads_leave_it_expected_only() -> None:
    assert "simulation_result" not in _sim(_propose("unknown")).required_deliverables
    assert "simulation_result" not in _sim(_propose(None)).required_deliverables


def test_a_caller_requirement_is_not_doubled() -> None:
    proposal = _propose(
        "98.1 N",
        [
            {
                "op": "add_deliverable",
                "phase": "simulation",
                "value": "simulation_result",
                "rationale": "verified by analysis",
            }
        ],
    )
    assert _sim(proposal).required_deliverables.count("simulation_result") == 1
    assert [op.kind for op in proposal.operations] == [OperationKind.ADD_DELIVERABLE]


def test_a_dropped_analysis_phase_is_left_to_the_invariants() -> None:
    proposal = _propose(
        "98.1 N",
        [{"op": "drop_phase", "phase": "simulation", "rationale": "verified by test instead"}],
    )
    assert all(
        op.phase_id != "simulation" or op.kind is OperationKind.DROP_PHASE
        for op in proposal.operations
    )
