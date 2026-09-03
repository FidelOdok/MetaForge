"""Unit tests for the chat-turn experience deposit (MET-567)."""

from __future__ import annotations

from typing import Any

import pytest

from api_gateway.chat import experience_adapter
from api_gateway.chat.experience_adapter import (
    chat_experience_enabled,
    init_chat_experience_recorder,
    record_chat_experience,
    summarize_turn,
    turn_importance,
)
from orchestrator.harness.react import ReActStep, ToolCall


class RecordingRecorder:
    def __init__(self, *, raises: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self._raises = raises

    async def record(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)
        if self._raises:
            raise RuntimeError("store down")


@pytest.fixture(autouse=True)
def _reset_recorder():
    yield
    experience_adapter._recorder = None  # noqa: SLF001


def _install(recorder: Any) -> None:
    experience_adapter._recorder = recorder  # noqa: SLF001


def _step(tool: str | None, *, error: str | None = None) -> ReActStep:
    return ReActStep(
        thought="t",
        tool_call=ToolCall(tool, {}) if tool else None,
        observation=None if error else {"ok": True},
        error=error,
    )


async def _record(**over: Any) -> bool:
    kwargs: dict[str, Any] = {
        "thread_id": "thread-1",
        "user_content": "design a hip bracket",
        "reply": "done",
        "steps": [_step("mcp_freecad_create_primitive"), _step(None)],
        "status": "completed",
        "stop_reason": "done",
        "duration_seconds": 4.0,
    }
    kwargs.update(over)
    return await record_chat_experience(**kwargs)


def test_summary_leads_with_the_goal_then_tools_then_failures():
    text = summarize_turn(
        user_content="design a hip bracket",
        steps=[
            _step("mcp_freecad_create_primitive"),
            _step("mcp_freecad_create_primitive"),
            _step("mcp_twin_commit_geometry", error="no geometry for (session, obj)"),
        ],
        reply="I built the bracket",
        stop_reason="done",
    )

    assert text.startswith("Goal: design a hip bracket")
    assert "mcp_freecad_create_primitive x2" in text
    assert "mcp_twin_commit_geometry: no geometry" in text
    assert "I built the bracket" in text


def test_summary_flags_a_turn_that_ended_early():
    text = summarize_turn(
        user_content="build an assembly",
        steps=[_step("mcp_freecad_create_primitive")],
        reply="partial",
        stop_reason="max_steps",
    )

    assert "Ended early: max_steps" in text


def test_a_failed_turn_scores_higher_than_a_clean_one():
    steps = [_step("mcp_kicad_run_erc")]

    failed = turn_importance(steps=steps, success=False, duration_seconds=1.0, summary="s")
    clean = turn_importance(steps=steps, success=True, duration_seconds=1.0, summary="s")

    assert failed > clean
    assert 0.0 <= clean <= 1.0


@pytest.mark.asyncio
async def test_a_tool_using_turn_is_recorded_with_provenance():
    recorder = RecordingRecorder()
    _install(recorder)

    assert await _record(project_id="55555555-5555-5555-5555-555555555555") is True

    call = recorder.calls[0]
    assert call["run_id"] == "thread-1"
    assert call["agent_code"] == "chat-harness"
    assert call["task_type"] == "chat_turn"
    assert call["success"] is True
    assert str(call["project_id"]) == "55555555-5555-5555-5555-555555555555"
    assert call["metadata"]["tools_used"] == ["mcp_freecad_create_primitive"]
    assert call["metadata"]["harness_path"] == "native"


@pytest.mark.asyncio
async def test_a_turn_that_called_no_tools_deposits_nothing():
    # The design-flow handlers drive run_chat_turn with max_steps=1 and no
    # bridge purely to extract JSON from a prompt. Those have no trajectory to
    # learn from and would otherwise swamp the corpus.
    recorder = RecordingRecorder()
    _install(recorder)

    assert await _record(steps=[_step(None)]) is False
    assert recorder.calls == []


@pytest.mark.asyncio
async def test_a_tool_error_marks_the_turn_unsuccessful():
    recorder = RecordingRecorder()
    _install(recorder)

    await _record(steps=[_step("mcp_twin_commit_geometry", error="no geometry")])

    call = recorder.calls[0]
    assert call["success"] is False
    assert call["error"] == "no geometry"
    assert call["metadata"]["error_count"] == 1


@pytest.mark.asyncio
async def test_no_recorder_wired_is_a_silent_no_op():
    assert chat_experience_enabled() is False
    assert await _record() is False


@pytest.mark.asyncio
async def test_a_broken_recorder_never_breaks_the_turn():
    _install(RecordingRecorder(raises=True))

    assert await _record() is False


def test_init_disables_itself_when_a_dependency_is_missing():
    init_chat_experience_recorder(None, object())
    assert chat_experience_enabled() is False

    init_chat_experience_recorder(object(), None)
    assert chat_experience_enabled() is False


def test_init_wires_a_recorder_when_both_dependencies_are_present():
    class Embeddings:
        async def embed(self, text: str) -> list[float]:
            return [0.0]

    init_chat_experience_recorder(object(), Embeddings())

    assert chat_experience_enabled() is True


class TestLiveTurnDeposit:
    """The deposit is wired into the real turn, not just callable on its own.

    MET-454 wired a recorder into MechanicalAgent only; every chat turn ran
    without one, so no amount of chat traffic filled ``agent_experiences``.
    """

    @pytest.mark.asyncio
    async def test_a_tool_using_chat_turn_deposits_one_experience(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import json

        from api_gateway.chat.harness_backend import run_chat_turn
        from orchestrator.harness.providers import ProviderSpec
        from skill_registry.mcp_bridge import InMemoryMcpBridge

        monkeypatch.setenv("METAFORGE_NATIVE_TOOLS", "false")
        recorder = RecordingRecorder()
        _install(recorder)

        bridge = InMemoryMcpBridge()
        bridge.register_tool("twin.query_node", capability="twin", name="Query Node")
        bridge.register_tool_response("twin.query_node", {"node": "N1", "mass_g": 42})

        calls = {"n": 0}

        async def invoke(spec: ProviderSpec, request: object) -> dict:
            calls["n"] += 1
            if calls["n"] == 1:
                return {
                    "text": json.dumps(
                        {
                            "thought": "look it up",
                            "tool": "mcp_twin_query_node",
                            "arguments": {"id": "N1"},
                        }
                    ),
                    "model": spec.model,
                }
            return {"text": '{"thought": "done", "final": "Mass is 42 g"}', "model": spec.model}

        out = await run_chat_turn(
            "What is the mass of N1?",
            invoke=invoke,
            max_steps=3,
            session_id="thread-9",
            mcp_bridge=bridge,
            project_id="66666666-6666-6666-6666-666666666666",
        )

        assert out == "Mass is 42 g"
        assert len(recorder.calls) == 1
        call = recorder.calls[0]
        assert call["run_id"] == "thread-9"
        assert call["metadata"]["tools_used"] == ["mcp_twin_query_node"]
        assert call["metadata"]["harness_path"] == "react"
        assert "Mass is 42 g" in call["result_summary"]

    @pytest.mark.asyncio
    async def test_a_toolless_chat_turn_deposits_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from api_gateway.chat.harness_backend import run_chat_turn
        from orchestrator.harness.providers import ProviderSpec

        monkeypatch.setenv("METAFORGE_NATIVE_TOOLS", "false")
        recorder = RecordingRecorder()
        _install(recorder)

        async def invoke(spec: ProviderSpec, request: object) -> dict:
            return {"text": '{"thought": "easy", "final": "42"}', "model": spec.model}

        assert await run_chat_turn("hello", invoke=invoke, max_steps=2) == "42"
        assert recorder.calls == []
