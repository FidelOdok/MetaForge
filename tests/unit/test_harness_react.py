"""Unit tests for the ReAct inner-loop step (MET-547, Phase 3)."""

from __future__ import annotations

import pytest

from orchestrator.harness import HarnessRuntime
from orchestrator.harness.react import (
    ReActAction,
    ReActStep,
    ToolCall,
    run_react,
)
from orchestrator.harness.tools import ToolRegistry


class ScriptedPolicy:
    """Returns a fixed sequence of actions, ignoring the trace."""

    def __init__(self, actions: list[ReActAction]) -> None:
        self._actions = actions
        self._i = 0

    async def next_action(self, goal: str, steps: list[ReActStep]) -> ReActAction:
        action = self._actions[min(self._i, len(self._actions) - 1)]
        self._i += 1
        return action


async def _double(args: dict[str, object]) -> dict[str, object]:
    return {"result": args["x"] * 2}  # type: ignore[operator]


def _runtime_with_tool(*, gate_check=None) -> HarnessRuntime:
    tools = ToolRegistry()
    tools.register_native("double", description="x2", input_schema={}, handler=_double)
    return HarnessRuntime.build(tools=tools, gate_check=gate_check)


@pytest.mark.asyncio
async def test_final_immediately() -> None:
    rt = HarnessRuntime.build()
    policy = ScriptedPolicy([ReActAction(thought="done", final_output="answer")])
    result = await run_react(rt, policy, "goal")
    assert result.status == "completed"
    assert result.output == "answer"
    assert len(result.steps) == 1


@pytest.mark.asyncio
async def test_tool_call_then_final() -> None:
    rt = _runtime_with_tool()
    policy = ScriptedPolicy(
        [
            ReActAction(thought="use tool", tool_call=ToolCall("double", {"x": 21})),
            ReActAction(thought="report", final_output="42"),
        ]
    )
    result = await run_react(rt, policy, "goal")
    assert result.status == "completed"
    assert result.output == "42"
    assert result.steps[0].observation == {"result": 42}
    assert result.steps[0].error is None


@pytest.mark.asyncio
async def test_tool_error_is_fed_back_not_fatal() -> None:
    rt = _runtime_with_tool()
    policy = ScriptedPolicy(
        [
            ReActAction(thought="bad tool", tool_call=ToolCall("missing", {})),
            ReActAction(thought="give up", final_output="handled"),
        ]
    )
    result = await run_react(rt, policy, "goal")
    assert result.status == "completed"
    assert result.output == "handled"
    assert result.steps[0].error is not None  # tool error captured, loop continued


@pytest.mark.asyncio
async def test_gate_blocked_tool_surfaces_as_error() -> None:
    tools = ToolRegistry()
    tools.register_native(
        "cut",
        description="destructive",
        input_schema={},
        handler=_double,
        required_gates=["approval"],
    )
    rt = HarnessRuntime.build(tools=tools, gate_check=lambda g: False)
    policy = ScriptedPolicy(
        [
            ReActAction(thought="try cut", tool_call=ToolCall("cut", {"x": 1})),
            ReActAction(thought="stop", final_output="blocked-ok"),
        ]
    )
    result = await run_react(rt, policy, "goal")
    assert result.status == "completed"
    assert "gate 'approval'" in result.steps[0].error


@pytest.mark.asyncio
async def test_exhausts_at_max_steps() -> None:
    rt = _runtime_with_tool()
    # Policy always calls a tool, never finalizes.
    policy = ScriptedPolicy([ReActAction(thought="loop", tool_call=ToolCall("double", {"x": 1}))])
    result = await run_react(rt, policy, "goal", max_steps=3)
    assert result.status == "exhausted"
    assert result.output is None
    assert len(result.steps) == 3
