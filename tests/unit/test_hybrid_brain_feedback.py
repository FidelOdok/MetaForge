"""A gate's retry or rework reason reaches the phase, whichever engine runs it.

Live evidence (run_08d61a4b18cd4ee5, 2026-11-15): the owner sent the run back
to Architecture with "use existing modules and boards, no custom PCB"; the
scripted architecture handler re-recorded the same custom-PCB budget, because
only the ReAct brain ever read ``context.retry_feedback``. Every scripted
handler (requirements, architecture, concept selection, V&V, manufacturing)
ignored every retry and rework reason.
"""

from __future__ import annotations

from typing import Any

import pytest

from api_gateway.runs.mech_handlers import HybridBrain
from api_gateway.runs.native_first import ELECTRONICS_NOTE, NativeFirstPhaseHandler
from orchestrator.design_flow.executor import FlowContext, PhaseOutcome
from orchestrator.design_flow.spec import get_flow

FEEDBACK = "REWORK (cycle 1): use existing modules and boards, no custom PCB."


def _phase(phase_id: str) -> Any:
    return next(p for p in get_flow("hardware_v1").phases if p.id == phase_id)


class _Scripted:
    def __init__(self) -> None:
        self.goals: list[str] = []

    async def run_phase(self, *, goal: str, phase: Any, context: Any) -> PhaseOutcome:
        self.goals.append(goal)
        return PhaseOutcome(summary="scripted", artifacts=[], status="completed")


@pytest.mark.asyncio
async def test_scripted_handler_receives_the_gate_feedback() -> None:
    scripted = _Scripted()
    brain = HybridBrain(handlers={"architecture": scripted}, fallback=_Scripted())
    ctx = FlowContext(goal="a night light", project_id="p1", retry_feedback=FEEDBACK)
    await brain.run_phase(goal="a night light", phase=_phase("architecture"), context=ctx)
    assert FEEDBACK in scripted.goals[0]
    assert scripted.goals[0].index(FEEDBACK) < scripted.goals[0].index("a night light")


@pytest.mark.asyncio
async def test_first_attempt_goal_is_unchanged() -> None:
    # negative control: no feedback, nothing added
    scripted = _Scripted()
    brain = HybridBrain(handlers={"architecture": scripted}, fallback=_Scripted())
    ctx = FlowContext(goal="a night light", project_id="p1")
    await brain.run_phase(goal="a night light", phase=_phase("architecture"), context=ctx)
    assert scripted.goals == ["a night light"]


@pytest.mark.asyncio
async def test_native_first_handler_is_not_given_the_feedback_twice() -> None:
    native, fallback = _Scripted(), _Scripted()

    async def present(project_id: str | None, since: float) -> set[str]:
        return set()  # forces the fallback, which must see the feedback once

    handler = NativeFirstPhaseHandler(
        native,
        fallback,
        present,
        required_types=("schematic",),
        note=ELECTRONICS_NOTE,
        label="electronics",
    )
    brain = HybridBrain(handlers={"electronics": handler}, fallback=_Scripted())
    ctx = FlowContext(goal="a night light", project_id="p1", retry_feedback=FEEDBACK)
    await brain.run_phase(goal="a night light", phase=_phase("electronics"), context=ctx)
    # the ReAct brain reads context.retry_feedback itself; its goal carries no copy
    assert FEEDBACK not in native.goals[0]
    assert fallback.goals[0].count(FEEDBACK) == 1
