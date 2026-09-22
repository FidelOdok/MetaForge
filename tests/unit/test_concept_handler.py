"""Concept-selection handler: the Decision Agent behind G5 (FORGE-73)."""

from __future__ import annotations

import pytest

from api_gateway.runs.concept_handlers import (
    GoalDrivenConceptSelectionHandler,
    _find_prior_decision_id,
    _normalize_concept_spec,
    _rationale,
)
from orchestrator.design_flow.executor import FlowContext, PhaseOutcome
from orchestrator.design_flow.spec import get_flow


def test_normalize_fills_defaults() -> None:
    s = _normalize_concept_spec({}, "a quadruped robot")
    assert s["selected_option"]
    assert s["selected_description"]
    assert s["alternatives"] and all(
        "option" in a and "reason_rejected" in a for a in s["alternatives"]
    )


def test_normalize_drops_alternatives_missing_an_option() -> None:
    s = _normalize_concept_spec(
        {
            "selected_option": "Direct-drive leg",
            "alternatives": [
                {
                    "option": "4-bar linkage leg",
                    "reason_rejected": "more parts, harder to manufacture",
                },
                {"reason_rejected": "no option name -- must be dropped"},
            ],
        },
        "a quadruped robot",
    )
    assert s["selected_option"] == "Direct-drive leg"
    assert len(s["alternatives"]) == 1
    assert s["alternatives"][0]["option"] == "4-bar linkage leg"


def test_rationale_names_selection_and_alternatives() -> None:
    s = _normalize_concept_spec(
        {
            "selected_option": "Direct-drive leg",
            "selected_description": "simplest, fewest failure points",
            "alternatives": [{"option": "4-bar linkage", "reason_rejected": "more complex"}],
        },
        "a quadruped robot",
    )
    text = _rationale(s, "a quadruped robot")
    assert "Direct-drive leg" in text
    assert "4-bar linkage" in text


class TestFindPriorDecisionId:
    def test_finds_a_real_uuid_artifact(self) -> None:
        phase = next(p for p in get_flow("hardware_v1").phases if p.id == "architecture")
        real_id = "11111111-1111-4111-8111-111111111111"
        outcome = PhaseOutcome(summary="arch done", artifacts=[f"design_decision:{real_id}"])
        ctx = FlowContext(goal="g", completed=[(phase, outcome)])
        assert _find_prior_decision_id(ctx, "architecture") == real_id

    def test_ignores_non_uuid_placeholder(self) -> None:
        phase = next(p for p in get_flow("hardware_v1").phases if p.id == "architecture")
        outcome = PhaseOutcome(summary="arch done", artifacts=["design_decision:architecture"])
        ctx = FlowContext(goal="g", completed=[(phase, outcome)])
        assert _find_prior_decision_id(ctx, "architecture") is None

    def test_none_when_phase_never_ran(self) -> None:
        ctx = FlowContext(goal="g", completed=[])
        assert _find_prior_decision_id(ctx, "architecture") is None


class _Bridge:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def invoke(self, tool: str, args: dict) -> dict:
        self.calls.append((tool, args))
        return {"status": "ok", "data": {"node_id": "concept1"}}


async def _fake_extract(goal, prior, *, provider, model):
    return _normalize_concept_spec(
        {
            "selected_option": "Direct-drive leg",
            "selected_description": "fewest failure points; satisfies the mass budget",
            "alternatives": [
                {
                    "option": "4-bar linkage leg",
                    "reason_rejected": "more parts, harder to manufacture",
                }
            ],
        },
        goal,
    )


@pytest.mark.asyncio
async def test_records_a_decision_with_alternatives_and_rationale() -> None:
    bridge = _Bridge()
    handler = GoalDrivenConceptSelectionHandler(bridge, extract=_fake_extract)
    phase = next(p for p in get_flow("hardware_v1").phases if p.id == "concept_selection")
    ctx = FlowContext(goal="a quadruped robot", project_id="p1", completed=[])

    outcome = await handler.run_phase(goal=ctx.goal, phase=phase, context=ctx)

    assert outcome.status == "completed"
    assert outcome.artifacts == ["design_decision:concept1"]
    tool, args = bridge.calls[0]
    assert tool == "twin.record_decision"
    assert args["title"] == "Concept selection: Direct-drive leg"
    assert args["alternatives"] == [
        {"option": "4-bar linkage leg", "reason_rejected": "more parts, harder to manufacture"}
    ]
    assert "parent_refs" not in args  # no architecture decision in context.completed


@pytest.mark.asyncio
async def test_links_to_a_real_prior_architecture_decision() -> None:
    bridge = _Bridge()
    handler = GoalDrivenConceptSelectionHandler(bridge, extract=_fake_extract)
    arch_phase = next(p for p in get_flow("hardware_v1").phases if p.id == "architecture")
    concept_phase = next(p for p in get_flow("hardware_v1").phases if p.id == "concept_selection")
    arch_id = "22222222-2222-4222-8222-222222222222"
    arch_outcome = PhaseOutcome(summary="arch done", artifacts=[f"design_decision:{arch_id}"])
    ctx = FlowContext(
        goal="a quadruped robot", project_id="p1", completed=[(arch_phase, arch_outcome)]
    )

    outcome = await handler.run_phase(goal=ctx.goal, phase=concept_phase, context=ctx)

    _, args = bridge.calls[0]
    assert args["parent_refs"] == [arch_id]
    assert "linked to the architecture decision" in outcome.summary
