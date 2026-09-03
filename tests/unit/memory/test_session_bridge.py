"""Unit tests for the session→experience bridge (MET-567)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest

from api_gateway.sessions.schemas import SessionEventResponse, SessionResponse
from digital_twin.memory.session_bridge import (
    SessionExperienceBridge,
    session_duration_seconds,
    session_importance,
    summarize_session,
)


class RecordingRecorder:
    def __init__(self, *, raises: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self._raises = raises

    async def record(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)
        if self._raises:
            raise RuntimeError("pgvector pool exhausted")


def _event(
    *,
    type: str = "action",
    message: str = "freecad.create_primitive",
    data: dict[str, object] | None = None,
) -> SessionEventResponse:
    return SessionEventResponse(
        id="e1",
        timestamp=datetime.now(UTC).isoformat(),
        type=type,
        agent_code="claude-code",
        message=message,
        data=data if data is not None else {"tool_id": message, "status": "ok"},
    )


def _session(
    *,
    status: str = "completed",
    events: list[SessionEventResponse] | None = None,
    project_id: str | None = None,
    summary: str | None = None,
) -> SessionResponse:
    started = datetime(2026, 9, 1, 10, 0, 0, tzinfo=UTC)
    return SessionResponse(
        id="11111111-1111-1111-1111-111111111111",
        agent_code="claude-code",
        task_type="cad_authoring",
        status=status,
        started_at=started.isoformat(),
        completed_at=(started + timedelta(minutes=5)).isoformat(),
        events=events if events is not None else [_event()],
        summary=summary,
        source="external",
        project_id=project_id,
    )


def test_summary_leads_with_tools_and_failures():
    session = _session(
        events=[
            _event(message="freecad.create_primitive"),
            _event(message="freecad.create_primitive"),
            _event(
                type="error",
                message="twin.commit_geometry failed",
                data={"tool_id": "twin.commit_geometry", "status": "error", "error": "no geometry"},
            ),
            _event(type="decision", message="chose aluminium for stiffness", data={}),
        ],
        summary="built a hip bracket",
    )

    text = summarize_session(session)

    assert "cad_authoring by claude-code — completed" in text
    assert "built a hip bracket" in text
    assert "freecad.create_primitive x2" in text
    assert "twin.commit_geometry: no geometry" in text
    assert "chose aluminium for stiffness" in text
    assert "3 tool call(s)" in text


def test_summary_is_capped():
    events = [_event(message=f"tool.number_{i}") for i in range(400)]

    text = summarize_session(_session(events=events))

    assert len(text) <= 2000


def test_summary_handles_a_session_with_no_tool_calls():
    text = summarize_session(_session(events=[_event(type="thought", message="thinking", data={})]))

    assert "Tools used: none" in text


def test_duration_is_derived_from_the_session_timestamps():
    assert session_duration_seconds(_session()) == 300.0


def test_duration_is_zero_when_timestamps_are_unusable():
    session = _session()
    session.started_at = "not-a-timestamp"

    assert session_duration_seconds(session) == 0.0


def test_a_failed_session_scores_higher_than_a_clean_one():
    # Failures are the most transferable lesson, which the shared importance
    # scorer already encodes (criticality 1.0 for FAILED vs 0.6 for COMPLETED).
    now = datetime(2026, 9, 1, 10, 5, 0, tzinfo=UTC)

    failed = session_importance(_session(status="failed"), now=now)
    clean = session_importance(_session(status="completed"), now=now)

    assert failed > clean
    assert 0.0 <= clean <= 1.0


@pytest.mark.asyncio
async def test_completed_session_is_recorded_with_provenance_metadata():
    recorder = RecordingRecorder()
    bridge = SessionExperienceBridge(recorder)
    project_id = "44444444-4444-4444-4444-444444444444"

    assert await bridge.on_session_completed(_session(project_id=project_id)) is True

    call = recorder.calls[0]
    assert call["run_id"] == "11111111-1111-1111-1111-111111111111"
    assert call["step_id"] == "session"
    assert call["agent_code"] == "claude-code"
    assert call["task_type"] == "cad_authoring"
    assert call["success"] is True
    assert call["error"] is None
    assert call["project_id"] == UUID(project_id)
    assert call["duration_seconds"] == 300.0
    assert call["metadata"]["tools_used"] == ["freecad.create_primitive"]
    assert call["metadata"]["recorded_by"] == "session_experience_bridge"


@pytest.mark.asyncio
async def test_a_failed_session_records_its_status_as_the_error():
    recorder = RecordingRecorder()

    await SessionExperienceBridge(recorder).on_session_completed(_session(status="abandoned"))

    call = recorder.calls[0]
    assert call["success"] is False
    assert call["error"] == "abandoned"


@pytest.mark.asyncio
async def test_an_eventless_session_deposits_nothing():
    # An implicit capture session that only ever saw a read-only call, then
    # rolled over on idle, has no trajectory worth embedding.
    recorder = RecordingRecorder()

    bridge = SessionExperienceBridge(recorder)

    assert await bridge.on_session_completed(_session(events=[])) is False
    assert recorder.calls == []


@pytest.mark.asyncio
async def test_a_broken_recorder_never_fails_the_completion():
    bridge = SessionExperienceBridge(RecordingRecorder(raises=True))

    assert await bridge.on_session_completed(_session()) is False


@pytest.mark.asyncio
async def test_plain_dict_events_are_accepted():
    # The Pg backend hands back pydantic events; test doubles and the sidecar's
    # own payloads are plain dicts. Both must summarise identically.
    recorder = RecordingRecorder()
    session = _session()
    session.events = [
        {"type": "action", "message": "kicad.run_erc", "data": {"tool_id": "kicad.run_erc"}}
    ]  # type: ignore[list-item]

    assert await SessionExperienceBridge(recorder).on_session_completed(session) is True
    assert recorder.calls[0]["metadata"]["tools_used"] == ["kicad.run_erc"]
