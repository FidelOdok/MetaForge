"""G0 (Intent) and G1 (Needs) gates -- Engineering Intent & Requirements
Harness (FORGE-48, epic FORGE-35). Shared across every built-in flow, ahead
of the existing "requirements" (G2) phase.
"""

from __future__ import annotations

from orchestrator.design_flow.spec import FLOWS, get_flow


def test_every_flow_starts_with_intent_then_needs_then_requirements() -> None:
    for flow_id, flow in FLOWS.items():
        ids = [p.id for p in flow.phases]
        assert ids[:3] == ["intent", "needs", "requirements"], (
            f"{flow_id} does not start intent -> needs -> requirements: {ids}"
        )


def test_intent_phase_requires_one_intent_entity() -> None:
    phase = next(p for p in get_flow("hardware_v1").phases if p.id == "intent")
    assert phase.required_deliverables == ("intent",)
    assert phase.enforce_deliverables is True
    assert phase.gate is not None
    assert phase.gate.name == "Intent sign-off"
    assert phase.gate.auto_approve is False
    assert phase.gate.criteria


def test_needs_phase_requires_one_stakeholder_need() -> None:
    phase = next(p for p in get_flow("hardware_v1").phases if p.id == "needs")
    assert phase.required_deliverables == ("stakeholder_need",)
    assert phase.enforce_deliverables is True
    assert phase.gate is not None
    assert phase.gate.name == "Needs sign-off"
    assert phase.gate.auto_approve is False
    assert phase.gate.criteria


def test_requirements_gate_gained_a_traceability_criterion() -> None:
    for flow_id, flow in FLOWS.items():
        phase = next(p for p in flow.phases if p.id == "requirements")
        assert phase.gate is not None
        assert any("trace" in c.lower() for c in phase.gate.criteria), (
            f"{flow_id}'s requirements gate has no traceability criterion"
        )


def test_intent_and_needs_phases_are_the_same_shared_instance_across_flows() -> None:
    """They're defined once and reused -- not accidentally re-authored
    per-flow with drifting objectives/criteria."""
    intent_phases = [next(p for p in flow.phases if p.id == "intent") for flow in FLOWS.values()]
    assert len({id(p) for p in intent_phases}) == 1

    needs_phases = [next(p for p in flow.phases if p.id == "needs") for flow in FLOWS.values()]
    assert len({id(p) for p in needs_phases}) == 1
