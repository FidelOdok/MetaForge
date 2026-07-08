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
async def test_on_step_receives_trace(tmp_path: Path) -> None:
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
