"""Unit tests: MechanicalAgent emits agent_experiences events (MET-454-fu)."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest

from domain_agents.mechanical.agent import MechanicalAgent, TaskRequest, TaskResult


class _CapturingRecorder:
    """In-memory ExperienceRecorder that captures every record call."""

    def __init__(self, raise_on: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self._raise = raise_on

    async def record(
        self,
        *,
        run_id: str,
        step_id: str,
        agent_code: str,
        task_type: str,
        success: bool,
        duration_seconds: float,
        result_summary: str,
        error: str | None = None,
        project_id: UUID | None = None,
        importance: float = 0.5,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if self._raise:
            raise RuntimeError("simulated recorder failure")
        self.calls.append(
            {
                "run_id": run_id,
                "step_id": step_id,
                "agent_code": agent_code,
                "task_type": task_type,
                "success": success,
                "duration_seconds": duration_seconds,
                "result_summary": result_summary,
                "error": error,
                "project_id": project_id,
                "importance": importance,
                "metadata": metadata,
            }
        )


class _StubMcp:
    """Minimal McpBridge stub — the agent doesn't call it for unknown tasks."""

    async def invoke(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
        raise NotImplementedError


def _make_agent(
    recorder: _CapturingRecorder | None,
    *,
    session_id: UUID | None = None,
) -> MechanicalAgent:
    return MechanicalAgent(
        twin=None,
        mcp=_StubMcp(),  # type: ignore[arg-type]
        session_id=session_id or uuid4(),
        experience_recorder=recorder,
    )


@pytest.mark.asyncio
async def test_run_task_records_one_experience_on_success():
    recorder = _CapturingRecorder()
    agent = _make_agent(recorder)
    # Unknown task_type falls into _run_hardcoded which short-circuits
    # with success=False + errors=[...]. That's still a recorded event.
    result = await agent.run_task(TaskRequest(task_type="unknown_task"))
    assert isinstance(result, TaskResult)
    assert len(recorder.calls) == 1
    call = recorder.calls[0]
    assert call["agent_code"] == "mechanical"
    assert call["task_type"] == "unknown_task"
    assert call["step_id"] == "unknown_task"
    assert call["run_id"] == str(agent.session_id)
    assert call["success"] is False
    assert call["duration_seconds"] > 0
    assert "mechanical" in call["result_summary"].lower()


@pytest.mark.asyncio
async def test_run_task_records_summary_carries_task_status():
    recorder = _CapturingRecorder()
    agent = _make_agent(recorder)
    await agent.run_task(TaskRequest(task_type="unknown_task"))
    summary = recorder.calls[0]["result_summary"]
    assert "failed" in summary.lower() or "succeeded" in summary.lower()
    assert "unknown_task" in summary


@pytest.mark.asyncio
async def test_run_task_skips_recording_when_no_recorder():
    agent = _make_agent(recorder=None)
    # Should not raise — the recorder is optional.
    result = await agent.run_task(TaskRequest(task_type="unknown_task"))
    assert isinstance(result, TaskResult)


@pytest.mark.asyncio
async def test_recorder_failure_does_not_break_agent():
    """Per Protocol contract, a recorder error must not propagate."""
    recorder = _CapturingRecorder(raise_on=True)
    agent = _make_agent(recorder)
    # The recorder.record raises RuntimeError; agent must still return
    # the task result cleanly.
    result = await agent.run_task(TaskRequest(task_type="unknown_task"))
    assert isinstance(result, TaskResult)
    # Recorder didn't get to append (it raised), but the agent kept going.
    assert recorder.calls == []


@pytest.mark.asyncio
async def test_record_carries_session_id_as_run_id():
    sid = uuid4()
    recorder = _CapturingRecorder()
    agent = _make_agent(recorder, session_id=sid)
    await agent.run_task(TaskRequest(task_type="unknown_task"))
    assert recorder.calls[0]["run_id"] == str(sid)


@pytest.mark.asyncio
async def test_record_metadata_carries_mode_and_parameters():
    recorder = _CapturingRecorder()
    agent = _make_agent(recorder)
    await agent.run_task(
        TaskRequest(
            task_type="unknown_task",
            parameters={"mesh_file": "x.inp", "tolerance": 0.1},
        )
    )
    md = recorder.calls[0]["metadata"]
    assert md["mode"] == "hardcoded"
    assert md["parameters_keys"] == ["mesh_file", "tolerance"]
