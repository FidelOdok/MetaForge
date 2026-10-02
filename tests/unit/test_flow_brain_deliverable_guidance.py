"""ReActPhaseBrain._deliverable_guidance -- the per-deliverable hints that
tell the phase brain exactly how to satisfy a gate (FORGE-48, epic FORGE-35).
"""

from __future__ import annotations

from api_gateway.runs.flow_brain import ReActPhaseBrain
from orchestrator.design_flow.executor import FlowContext
from orchestrator.design_flow.spec import get_flow


def test_intent_deliverable_hints_at_the_engineering_entity_tool() -> None:
    phase = next(p for p in get_flow("hardware_v1").phases if p.id == "intent")
    ctx = FlowContext(goal="a desktop quadruped", project_id="p1", completed=[])
    text = ReActPhaseBrain._deliverable_guidance(phase, ctx)
    assert "record-engineering-entity" in text
    assert "entity_type='intent'" in text
    assert "project_id=p1" in text


def test_needs_deliverable_hints_at_linking_back_to_the_intent() -> None:
    phase = next(p for p in get_flow("hardware_v1").phases if p.id == "needs")
    ctx = FlowContext(goal="a desktop quadruped", project_id="p1", completed=[])
    text = ReActPhaseBrain._deliverable_guidance(phase, ctx)
    assert "record-engineering-entity" in text
    assert "entity_type='stakeholder_need'" in text
    assert "parent_refs" in text
    assert "motivates" in text


def test_missing_project_id_falls_back_to_a_placeholder() -> None:
    phase = next(p for p in get_flow("hardware_v1").phases if p.id == "intent")
    ctx = FlowContext(goal="a desktop quadruped", project_id=None, completed=[])
    text = ReActPhaseBrain._deliverable_guidance(phase, ctx)
    assert "<the project>" in text


def _known_deliverable_types() -> set[str]:
    """Every type a template requires or expects, plus the types the
    generator's invariants and ``twin.record_document`` name."""
    from orchestrator.design_flow.invariants import (
        ALTERNATIVE_VERIFICATION_ARTIFACT,
        PHYSICAL_EVIDENCE_ARTIFACTS,
    )
    from orchestrator.design_flow.spec import _load_flows
    from tool_registry.tools.twin.adapter import TwinServer

    types: set[str] = {
        ALTERNATIVE_VERIFICATION_ARTIFACT,
        *PHYSICAL_EVIDENCE_ARTIFACTS,
        *TwinServer._DOCUMENT_TYPES,
    }
    for flow in _load_flows().values():
        for phase in flow.phases:
            types.update(phase.required_deliverables)
            types.update(phase.expected_artifacts)
    return types


def test_every_addable_or_template_deliverable_has_a_specific_hint() -> None:
    from api_gateway.runs.flow_brain import deliverable_hints

    hints = deliverable_hints("p1")
    missing = sorted(_known_deliverable_types() - hints.keys())
    assert not missing, f"deliverable types with only the generic hint: {missing}"


def test_hints_name_the_exact_tool_and_type() -> None:
    from api_gateway.runs.flow_brain import deliverable_hints

    hints = deliverable_hints("p1")
    assert "record-document" in hints["prd"] and "document_type='prd'" in hints["prd"]
    assert "record-constraint-set" in hints["constraint_set"]
    for t in ("simulation_result", "load_case"):
        assert f"document_type='{t}'" in hints[t]
    assert "record-component-selection" in hints["bom"]
    assert all("project_id=p1" in hints[t] for t in ("prd", "constraint_set", "bom"))
