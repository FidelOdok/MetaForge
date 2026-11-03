"""concept_v1: decide what to build, and stop before any CAD (FORGE-767).

A dedicated flow for the concept: intent with the deciding questions, needs,
requirements, feasibility, the manufacturing route and concept design, then a
verification plan that hands the concept on with its checks named. Its
shared phases are mech_v1's, so a concept approved here is the one mech_v1
would have reached; this suite fails if the two drift apart.
"""

from __future__ import annotations

from typing import Any

from orchestrator.design_flow.spec import get_flow
from orchestrator.design_flow.templates import load_templates

#: The phases shared with mech_v1, verbatim.
SHARED_PHASES = [
    "intent",
    "needs",
    "requirements",
    "feasibility",
    "route_selection",
    "concept_selection",
]


def _phases(flow_id: str) -> dict[str, Any]:
    return {p.id: p for p in load_templates()[flow_id].definition.phases}


def test_it_runs_to_the_concept_and_hands_it_on_with_a_verification_plan() -> None:
    assert list(_phases("concept_v1")) == [*SHARED_PHASES, "verification_plan"]
    plan = get_flow("concept_v1").phases[-1]
    assert plan.id == "verification_plan"
    # The rule that requirements end up verified holds: the plan names how.
    assert "test_plan" in plan.expected_artifacts
    assert "acceptance criterion" in plan.objective


def test_no_phase_authors_cad() -> None:
    for phase in _phases("concept_v1").values():
        assert "cad_model" not in phase.required_deliverables
        assert "cad_model" not in phase.expected_artifacts


def test_the_concept_ends_at_the_pick_a_direction_gate() -> None:
    concept = _phases("concept_v1")["concept_selection"]
    assert set(concept.required_deliverables) == {"design_sketch", "design_decision"}
    assert concept.gate is not None and concept.gate.gate_id == "G5"
    assert "the person picks at this gate" in concept.objective


def test_its_phases_are_mech_v1s_so_they_cannot_drift() -> None:
    concept, mech = _phases("concept_v1"), _phases("mech_v1")
    for phase_id in SHARED_PHASES:
        assert concept[phase_id] == mech[phase_id], f"{phase_id} drifted from mech_v1"
