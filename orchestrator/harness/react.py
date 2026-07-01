"""ReAct inner-loop step (MET-547, Phase 3).

Each harness agent (Planner / Generator / Evaluator) runs a ReAct loop:
reason -> act (call a tool) -> observe -> repeat, until it emits a final answer
or hits the step cap. Tool calls go through :class:`HarnessRuntime.call_tool`,
so gate preconditions are enforced on every action.

The decision of what to do next is a :class:`Policy` -- injected, so the loop
is exercised in tests with a scripted policy and no live model. A real policy
wraps :meth:`HarnessRuntime.complete` to ask a model for the next action.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import structlog

from orchestrator.harness.runtime import HarnessRuntime

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class ToolCall:
    """A request to invoke a registered tool."""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ReActAction:
    """What the policy decided to do this turn.

    Exactly one of ``tool_call`` (keep going) or ``final_output`` (stop) is set.
    """

    thought: str
    tool_call: ToolCall | None = None
    final_output: Any | None = None

    @property
    def is_final(self) -> bool:
        return self.tool_call is None


@dataclass(frozen=True)
class ReActStep:
    """One executed turn: the thought, the action, and what came back."""

    thought: str
    tool_call: ToolCall | None
    observation: Any | None = None
    error: str | None = None


@dataclass
class ReActResult:
    """Outcome of a ReAct loop."""

    status: str  # "completed" | "exhausted"
    output: Any | None
    steps: list[ReActStep]


@runtime_checkable
class Policy(Protocol):
    """Decides the next action given the goal and the trace so far."""

    async def next_action(self, goal: str, steps: list[ReActStep]) -> ReActAction: ...


async def run_react(
    runtime: HarnessRuntime,
    policy: Policy,
    goal: str,
    *,
    max_steps: int = 8,
) -> ReActResult:
    """Drive the reason/act/observe loop until final or the step cap.

    A tool error is fed back as an observation (``error`` set) and the loop
    continues, so the policy can recover or give up -- it is not fatal.
    """
    steps: list[ReActStep] = []
    for step_no in range(1, max_steps + 1):
        action = await policy.next_action(goal, steps)

        if action.is_final:
            steps.append(
                ReActStep(thought=action.thought, tool_call=None, observation=action.final_output)
            )
            logger.info("react_completed", goal=goal, steps=step_no)
            return ReActResult(status="completed", output=action.final_output, steps=steps)

        call = action.tool_call
        assert call is not None  # not is_final => tool_call set
        try:
            observation = await runtime.call_tool(call.name, call.arguments)
            steps.append(ReActStep(action.thought, call, observation=observation))
        except Exception as exc:  # noqa: BLE001 - surface tool failure to the policy, don't abort
            steps.append(ReActStep(action.thought, call, error=str(exc)))
            logger.warning("react_tool_error", tool=call.name, error=str(exc))

    logger.info("react_exhausted", goal=goal, steps=max_steps)
    return ReActResult(status="exhausted", output=None, steps=steps)
