"""Agent-step emission for chat legibility (MET-552).

The harness computes a full ReAct trace (tool calls, observations, reasoning)
but historically discarded it. ``run_chat_turn_streaming`` now surfaces it via
``on_step`` so the UI can render a tool-call timeline.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from api_gateway.chat.harness_backend import (
    _json_safe,
    _step_to_dict,
    run_chat_turn_streaming,
)
from orchestrator.harness.providers import CredentialStore, ProviderSpec
from orchestrator.harness.react import ReActStep, ToolCall


async def _final_invoke(spec: ProviderSpec, request: object) -> dict[str, Any]:
    return {"text": '{"thought": "all done", "final": "the answer"}', "model": spec.model}


def _no_stream(spec: ProviderSpec, request: object):  # type: ignore[no-untyped-def]
    async def gen():  # type: ignore[no-untyped-def]
        yield "the answer"

    return gen()


# --- pure helpers ----------------------------------------------------------


def test_json_safe_coerces_unserializable() -> None:
    class Weird:
        def __str__(self) -> str:
            return "weird!"

    out = _json_safe({"a": [1, "x", Weird()], "b": {"c": True}})
    assert out == {"a": [1, "x", "weird!"], "b": {"c": True}}


def test_step_to_dict_tool_step() -> None:
    step = ReActStep(
        thought="query the twin",
        tool_call=ToolCall(name="twin.get_node", arguments={"id": "n1"}),
        observation={"status": "ok"},
    )
    d = _step_to_dict(step, 0)
    assert d["tool"] == "twin.get_node"
    assert d["arguments"] == {"id": "n1"}
    assert d["observation"] == {"status": "ok"}
    assert d["final"] is False


def test_step_to_dict_final_step_omits_observation() -> None:
    # Final step carries the answer as observation — omitted (streamed separately).
    step = ReActStep(thought="done", tool_call=None, observation="the answer text")
    d = _step_to_dict(step, 3)
    assert d["final"] is True
    assert d["tool"] is None
    assert d["observation"] is None
    assert d["thought"] == "done"


# --- emission through the turn --------------------------------------------


@pytest.mark.asyncio
async def test_on_step_receives_trace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # This test scripts ReAct-protocol replies — pin the ReAct path
    # explicitly (MET-575: the path now follows the resolved provider,
    # and the all-defaults resolution is anthropic → native).
    monkeypatch.setenv("METAFORGE_NATIVE_TOOLS", "false")
    steps: list[dict[str, Any]] = []

    async def on_step(s: dict[str, Any]) -> None:
        steps.append(s)

    async def on_delta(_d: str) -> None:
        return None

    await run_chat_turn_streaming(
        "hi",
        on_delta=on_delta,
        on_step=on_step,
        invoke=_final_invoke,
        stream_invoke=_no_stream,
        credentials=CredentialStore(tmp_path / "c.json"),
    )

    # A no-tool turn still emits its final reasoning step.
    assert len(steps) == 1
    assert steps[0]["final"] is True
    assert steps[0]["thought"] == "all done"


# --- MET-590: steps stream LIVE during the loop ------------------------------------


@pytest.mark.asyncio
async def test_steps_stream_during_loop_not_after(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The step for tool call N must reach on_step BEFORE the model is asked
    for step N+1 — that liveness is what lets clients keep long turns alive
    (a fixed client timeout killed a healthy 5-minute CAD turn; MET-590)."""
    monkeypatch.setenv("METAFORGE_NATIVE_TOOLS", "false")
    emitted: list[dict[str, Any]] = []
    seen_at_invoke: list[int] = []

    replies = iter(
        [
            '{"thought": "look", "tool": "echo", "arguments": {"n": 1}}',
            '{"thought": "again", "tool": "echo", "arguments": {"n": 2}}',
            '{"final": "done"}',
        ]
    )

    async def invoke(spec: Any, request: dict[str, Any]) -> dict[str, Any]:
        seen_at_invoke.append(len(emitted))
        return {"text": next(replies), "model": spec.model}

    async def on_step(s: dict[str, Any]) -> None:
        emitted.append(s)

    async def on_delta(_d: str) -> None:
        return None

    out = await run_chat_turn_streaming(
        "run echo twice",
        on_delta=on_delta,
        on_step=on_step,
        invoke=invoke,
        stream_invoke=_no_stream,
        credentials=CredentialStore(tmp_path / "c.json"),
    )
    assert out == "done"
    # 2 tool steps + the final reasoning step, each exactly once (no
    # post-loop duplicate emission).
    assert len(emitted) == 3
    # Liveness: by the 2nd model call one step had already streamed; by the
    # 3rd, two had. (First call necessarily sees zero.)
    assert seen_at_invoke == [0, 1, 2]


@pytest.mark.asyncio
async def test_native_loop_streams_steps_live(tmp_path: Path) -> None:
    from orchestrator.harness.native_tools import run_native_tools
    from orchestrator.harness.providers import load_provider_config
    from orchestrator.harness.runtime import HarnessRuntime
    from orchestrator.harness.tools import ToolRegistry

    tools = ToolRegistry()

    async def echo(arguments: dict[str, object]) -> object:
        return {"ok": True}

    tools.register_native("echo", description="echo", input_schema={"type": "object"}, handler=echo)
    rt = HarnessRuntime.build(
        load_provider_config(
            {"roles": {"generator": [{"provider": "anthropic", "model": "claude-opus-4-8"}]}}
        ),
        tools=tools,
    )

    emitted: list[int] = []
    seen_at_invoke: list[int] = []
    calls = {"n": 0}

    async def invoke(spec: Any, request: dict[str, Any]) -> dict[str, Any]:
        seen_at_invoke.append(len(emitted))
        calls["n"] += 1
        if calls["n"] <= 2:
            return {
                "text": "",
                "tool_calls": [{"id": f"c{calls['n']}", "name": "echo", "arguments": {}}],
                "model": spec.model,
            }
        return {"text": "done", "model": spec.model}

    async def on_step(step: Any, index: int) -> None:
        emitted.append(index)

    result = await run_native_tools(rt, "go", invoke=invoke, max_steps=5, on_step=on_step)
    assert result.status == "completed"
    assert emitted == [0, 1]
    assert seen_at_invoke == [0, 1, 2]  # each step streamed before the next model call
