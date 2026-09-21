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
