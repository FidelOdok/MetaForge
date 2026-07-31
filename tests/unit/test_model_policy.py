"""Unit tests for the model-backed ReAct policy (MET-548). Network-free."""

from __future__ import annotations

import pytest

from orchestrator.harness import HarnessRuntime
from orchestrator.harness.policy import ModelPolicy, parse_action
from orchestrator.harness.providers import ProviderSpec, load_provider_config
from orchestrator.harness.react import ReActParseError, run_react
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


def test_parse_unstructured_raises() -> None:
    """A model that ignores the protocol must not silently "succeed" with a
    narrative substituted for real tool calls (the exact hallucination this
    guards against: a plausible-sounding answer that never called a tool)."""
    with pytest.raises(ReActParseError):
        parse_action("I could not produce JSON but the answer is 5.")


def test_parse_json_missing_tool_and_final_raises() -> None:
    """A JSON object is present but doesn't follow either defined shape."""
    with pytest.raises(ReActParseError):
        parse_action('{"thought": "I built the assembly and committed it."}')


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
async def test_policy_warns_against_repeating_a_successful_call() -> None:
    """Live-caught: the model would occasionally re-issue a tool call whose
    result was already visible in the trace as successful (harmless but
    wasteful — extra steps, extra latency). The system prompt already warned
    against repeating a FAILED call; this checks the matching warning for an
    already-SUCCEEDED one is present too."""
    rt = HarnessRuntime.build(CONFIG)

    seen: dict[str, object] = {}

    async def invoke(spec: ProviderSpec, request: object) -> dict:
        seen["system"] = request["system"]  # type: ignore[index]
        return {"text": '{"final": "done"}', "model": spec.model}

    await ModelPolicy(rt, invoke=invoke).next_action("goal", [])
    system = seen["system"]
    assert isinstance(system, str)
    assert "already SUCCEEDED" in system


@pytest.mark.asyncio
async def test_policy_includes_argument_schema_in_prompt() -> None:
    """Live-caught: without a schema, a ReAct-path model has no way to know a
    tool's real argument names and guesses wrong (missing/misnamed required
    fields) — every attempted tool call then fails. The catalog must carry the
    same input_schema the native tool-calling path already sends."""
    tools = ToolRegistry()

    async def _h(args: dict[str, object]) -> dict[str, object]:
        return {"result": args.get("kind")}

    tools.register_native(
        "freecad.create_primitive",
        description="Create a primitive solid",
        input_schema={
            "type": "object",
            "properties": {"kind": {"type": "string"}, "session_id": {"type": "string"}},
            "required": ["kind", "session_id"],
        },
        handler=_h,
    )
    rt = HarnessRuntime.build(CONFIG, tools=tools)

    seen: dict[str, object] = {}

    async def invoke(spec: ProviderSpec, request: object) -> dict:
        seen["system"] = request["system"]  # type: ignore[index]
        return {"text": '{"final": "done"}', "model": spec.model}

    await ModelPolicy(rt, invoke=invoke).next_action("goal", [])
    system = seen["system"]
    assert isinstance(system, str)
    assert '"required":["kind","session_id"]' in system.replace(" ", "")


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


@pytest.mark.asyncio
async def test_react_recovers_from_a_malformed_reply_instead_of_hallucinating() -> None:
    """The exact scenario this fix targets: the model narrates a result in
    plain prose instead of calling the tool it was asked to use. The loop must
    NOT accept that narrative as the final answer — it should feed back a
    parse error and give the model another chance, which then calls the real
    tool and finalizes correctly."""
    tools = ToolRegistry()

    async def _double(args: dict[str, object]) -> dict[str, object]:
        return {"result": args["x"] * 2}  # type: ignore[operator]

    tools.register_native("double", description="x2", input_schema={}, handler=_double)
    rt = HarnessRuntime.build(CONFIG, tools=tools)

    policy = ModelPolicy(
        rt,
        invoke=_scripted_invoke(
            "Sure! I ran the tool and the result is 42.",  # malformed: no JSON at all
            '{"thought": "for real this time", "tool": "double", "arguments": {"x": 21}}',
            '{"thought": "report", "final": "the answer is 42"}',
        ),
    )
    result = await run_react(rt, policy, "double 21", max_steps=5)

    assert result.status == "completed"
    assert result.output == "the answer is 42"
    # The malformed first reply is recorded as a step with an error, NOT
    # silently accepted as the final answer — and the real tool call that
    # followed actually ran.
    assert result.steps[0].tool_call.name == "(invalid_reply)"
    assert result.steps[0].error is not None
    assert result.steps[1].observation == {"result": 42}


@pytest.mark.asyncio
async def test_policy_renders_history_into_prompt() -> None:
    """MET-565: the ReAct path used to drop conversation history entirely —
    every non-native-tools provider answered each turn with total amnesia.
    History handed to the policy must reach the model, rendered as text inside
    the goal message (not as real chat turns, which would teach the JSON-only
    protocol to answer in prose)."""
    rt = HarnessRuntime.build(CONFIG)

    seen: dict[str, object] = {}

    async def invoke(spec: ProviderSpec, request: object) -> dict:
        seen["content"] = request["messages"][0]["content"]  # type: ignore[index]
        return {"text": '{"final": "done"}', "model": spec.model}

    history = [
        {"role": "user", "content": "my bracket is 40mm wide"},
        {"role": "assistant", "content": "Noted — a 40mm-wide bracket."},
    ]
    await ModelPolicy(rt, invoke=invoke, history=history).next_action("how wide is it?", [])

    content = seen["content"]
    assert isinstance(content, str)
    assert "my bracket is 40mm wide" in content
    assert "Conversation so far:" in content
    assert content.index("40mm") < content.index("Goal:")  # history precedes the goal


@pytest.mark.asyncio
async def test_policy_without_history_omits_preamble() -> None:
    rt = HarnessRuntime.build(CONFIG)

    seen: dict[str, object] = {}

    async def invoke(spec: ProviderSpec, request: object) -> dict:
        seen["content"] = request["messages"][0]["content"]  # type: ignore[index]
        return {"text": '{"final": "done"}', "model": spec.model}

    await ModelPolicy(rt, invoke=invoke).next_action("goal", [])
    assert "Conversation so far:" not in str(seen["content"])
