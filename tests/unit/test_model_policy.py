"""Unit tests for the model-backed ReAct policy (MET-548). Network-free."""

from __future__ import annotations

import pytest

from orchestrator.harness import HarnessRuntime
from orchestrator.harness.policy import ModelPolicy, parse_action
from orchestrator.harness.providers import ProviderSpec, load_provider_config
from orchestrator.harness.react import run_react
from orchestrator.harness.tools import ToolRegistry

CONFIG = load_provider_config(
    {"roles": {"generator": [{"provider": "anthropic", "model": "claude-opus-4-8"}]}}
)


# --- parse_action ----------------------------------------------------------
def test_parse_final_json() -> None:
    a = parse_action('{"thought": "done", "final": "42"}')
    assert a.is_final and a.final_output == "42" and a.thought == "done"


def test_parse_tool_json() -> None:
    a = parse_action('{"thought": "compute", "tool": "double", "arguments": {"x": 21}}')
    assert not a.is_final
    assert a.tool_call.name == "double" and a.tool_call.arguments == {"x": 21}


def test_parse_fenced_json() -> None:
    a = parse_action('here you go:\n```json\n{"final": "ok"}\n```\n')
    assert a.is_final and a.final_output == "ok"


def test_parse_unstructured_is_final() -> None:
    a = parse_action("I could not produce JSON but the answer is 5.")
    assert a.is_final and "answer is 5" in a.final_output


# --- ModelPolicy -----------------------------------------------------------
def _scripted_invoke(*replies: str):
    calls = {"n": 0}

    async def invoke(spec: ProviderSpec, request: object) -> dict:
        i = min(calls["n"], len(replies) - 1)
        calls["n"] += 1
        return {"text": replies[i], "model": spec.model}

    return invoke


@pytest.mark.asyncio
async def test_next_action_parses_model_reply() -> None:
    rt = HarnessRuntime.build(CONFIG)
    policy = ModelPolicy(rt, invoke=_scripted_invoke('{"tool": "double", "arguments": {"x": 2}}'))
    action = await policy.next_action("goal", [])
    assert action.tool_call.name == "double"


@pytest.mark.asyncio
async def test_policy_lists_tools_in_prompt() -> None:
    tools = ToolRegistry()

    async def _h(args: dict[str, object]) -> dict[str, object]:
        return {"result": args.get("x")}

    tools.register_native("double", description="doubles x", input_schema={}, handler=_h)
    rt = HarnessRuntime.build(CONFIG, tools=tools)

    seen: dict[str, object] = {}

    async def invoke(spec: ProviderSpec, request: object) -> dict:
        seen["system"] = request["system"]  # type: ignore[index]
        return {"text": '{"final": "done"}', "model": spec.model}

    await ModelPolicy(rt, invoke=invoke).next_action("goal", [])
    assert "double: doubles x" in seen["system"]  # type: ignore[operator]


@pytest.mark.asyncio
async def test_model_policy_drives_react_loop() -> None:
    """End to end: model calls a tool, sees the result, then finalizes."""
    tools = ToolRegistry()

    async def _double(args: dict[str, object]) -> dict[str, object]:
        return {"result": args["x"] * 2}  # type: ignore[operator]

    tools.register_native("double", description="x2", input_schema={}, handler=_double)
    rt = HarnessRuntime.build(CONFIG, tools=tools)

    policy = ModelPolicy(
        rt,
        invoke=_scripted_invoke(
            '{"thought": "use tool", "tool": "double", "arguments": {"x": 21}}',
            '{"thought": "report", "final": "the answer is 42"}',
        ),
    )
    result = await run_react(rt, policy, "double 21", max_steps=5)
    assert result.status == "completed"
    assert result.output == "the answer is 42"
    assert result.steps[0].observation == {"result": 42}
