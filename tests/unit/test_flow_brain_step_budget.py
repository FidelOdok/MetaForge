"""Per-phase step budget on ReActPhaseBrain (FORGE-501)."""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from api_gateway.runs import flow_brain
from api_gateway.runs.flow_brain import ReActPhaseBrain, phase_is_heavy, phase_step_budget
from orchestrator.design_flow.executor import FlowContext
from orchestrator.design_flow.spec import Phase, get_flow


def _phase(required: tuple[str, ...] = (), expected: tuple[str, ...] = ()) -> Phase:
    base = get_flow("mech_v1").phases[0]
    return dataclasses.replace(base, required_deliverables=required, expected_artifacts=expected)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("METAFORGE_FLOW_PHASE_MAX_STEPS", raising=False)
    monkeypatch.delenv("METAFORGE_FLOW_PHASE_MAX_STEPS_HEAVY", raising=False)


def test_defaults_light_and_heavy() -> None:
    assert phase_step_budget(_phase()) == 24
    assert phase_step_budget(_phase(required=("cad_model",))) == 60
    assert phase_step_budget(_phase(expected=("simulation_result",))) == 60
    assert phase_is_heavy(_phase(required=("cad_model",)))
    assert not phase_is_heavy(_phase(required=("design_decision",)))


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("METAFORGE_FLOW_PHASE_MAX_STEPS", "10")
    monkeypatch.setenv("METAFORGE_FLOW_PHASE_MAX_STEPS_HEAVY", "99")
    assert phase_step_budget(_phase()) == 10
    assert phase_step_budget(_phase(required=("cad_model",))) == 99


@pytest.mark.parametrize("bad", ["abc", "0", "-3"])
def test_invalid_env_falls_back(monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
    monkeypatch.setenv("METAFORGE_FLOW_PHASE_MAX_STEPS", bad)
    assert phase_step_budget(_phase()) == 24


def test_explicit_override_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("METAFORGE_FLOW_PHASE_MAX_STEPS_HEAVY", "99")
    assert phase_step_budget(_phase(required=("cad_model",)), 7) == 7


@pytest.mark.asyncio
async def test_run_phase_passes_budget_and_reports_it_on_exhaustion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    async def fake_turn(prompt: str, **kwargs: Any) -> str:
        seen.update(kwargs)
        return "I couldn't converge on an answer within the step budget."

    monkeypatch.setattr(flow_brain, "run_chat_turn", fake_turn)
    brain = ReActPhaseBrain(mcp_bridge=None)
    phase = _phase(required=("cad_model",))
    ctx = FlowContext(goal="a bracket", project_id="p1", completed=[])
    outcome = await brain.run_phase(goal=ctx.goal, phase=phase, context=ctx)
    assert seen["max_steps"] == 60
    assert outcome.status == "exhausted"
    assert "budget of 60 steps" in outcome.summary
