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

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import structlog

from orchestrator.harness.runtime import HarnessRuntime

logger = structlog.get_logger(__name__)

# MET-590: live step observer — called as each step lands, (step, index).
OnStep = Callable[["ReActStep", int], Awaitable[None]]


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


class ReActParseError(Exception):
    """A model's reply didn't follow the ReAct JSON protocol (no tool/final key).

    Raised by ``policy.next_action`` (see ``policy.parse_action``) instead of
    silently treating stray prose as a final answer — a model that ignores the
    protocol must not be able to "succeed" with a plausible-sounding narrative
    substituted for the tool calls it never made. ``run_react`` catches this
    the same way it catches a tool-call failure: fed back as an observation,
    not fatal, so the model gets a concrete chance to self-correct.
    """


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
    on_step: OnStep | None = None,
) -> ReActResult:
    """Drive the reason/act/observe loop until final or the step cap.

    A tool error, or a policy reply that fails the ReAct protocol
    (``ReActParseError``), is fed back as an observation (``error`` set) and
    the loop continues, so the policy can recover or give up — neither is
    fatal to the turn.
    """
    steps: list[ReActStep] = []

    async def _emit(step: ReActStep) -> None:
        # MET-590: live progress — a broken observer must never break the turn.
        if on_step is None:
            return
        try:
            await on_step(step, len(steps) - 1)
        except Exception as exc:  # noqa: BLE001 - observer is best-effort
            logger.warning("react_on_step_failed", error=str(exc))

    for step_no in range(1, max_steps + 1):
        try:
            action = await policy.next_action(goal, steps)
        except ReActParseError as exc:
            # No tool_call was ever decided — a synthetic marker lets this
            # render through the same "- called X -> error" trace line the
            # model already sees for a real tool failure, and keeps it
            # visible to ``ModelPolicy._render_trace`` (which only renders
            # steps that have a ``tool_call``).
            steps.append(
                ReActStep(
                    thought="(malformed reply)",
                    tool_call=ToolCall("(invalid_reply)", {}),
                    error=str(exc),
                )
            )
            await _emit(steps[-1])
            logger.warning("react_parse_error", goal=goal, step=step_no, error=str(exc))
            continue

        if action.is_final:
            steps.append(
                ReActStep(thought=action.thought, tool_call=None, observation=action.final_output)
            )
            await _emit(steps[-1])
            logger.info("react_completed", goal=goal, steps=step_no)
            return ReActResult(status="completed", output=action.final_output, steps=steps)

        call = action.tool_call
        assert call is not None  # not is_final => tool_call set
        try:
            observation = await runtime.call_tool(call.name, call.arguments)
            steps.append(ReActStep(action.thought, call, observation=observation))
            await _emit(steps[-1])
        except Exception as exc:  # noqa: BLE001 - surface tool failure to the policy, don't abort
            steps.append(ReActStep(action.thought, call, error=str(exc)))
            await _emit(steps[-1])
            logger.warning("react_tool_error", tool=call.name, error=str(exc))

    logger.info("react_exhausted", goal=goal, steps=max_steps)
    return ReActResult(status="exhausted", output=None, steps=steps)
