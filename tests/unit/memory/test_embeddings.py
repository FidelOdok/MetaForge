"""Unit tests for ``digital_twin.memory.embeddings``."""

from __future__ import annotations

from datetime import UTC, datetime

from digital_twin.memory.embeddings import event_to_text
from orchestrator.event_bus.events import Event, EventType


def _event(event_type: EventType, data: dict) -> Event:
    return Event(
        id="evt-1",
        type=event_type,
        timestamp=datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC).isoformat(),
        source="scheduler",
        data=data,
    )


def test_event_to_text_includes_outcome_and_agent():
    event = _event(
        EventType.AGENT_TASK_COMPLETED,
        {"agent_code": "mechanical", "run_id": "r1", "step_id": "s1"},
    )
    text = event_to_text(event)
    assert "agent=mechanical" in text
    assert "outcome=completed" in text
    assert "run=r1" in text
    assert "step=s1" in text


def test_event_to_text_is_deterministic():
    event = _event(
        EventType.AGENT_TASK_FAILED,
        {
            "agent_code": "fw",
            "run_id": "r",
            "step_id": "s",
            "error": "out  of   memory",
        },
    )
    assert event_to_text(event) == event_to_text(event)


def test_event_to_text_summarizes_dict_result_via_known_keys():
    event = _event(
        EventType.AGENT_TASK_COMPLETED,
        {
            "agent_code": "elec",
            "result": {"summary": "ERC pass with 0 warnings", "warnings": []},
        },
    )
    text = event_to_text(event)
    assert "result=ERC pass with 0 warnings" in text


def test_event_to_text_collapses_error_whitespace():
    event = _event(
        EventType.AGENT_TASK_FAILED,
        {"agent_code": "mech", "error": "stress   exceeded\n   allowable"},
    )
    text = event_to_text(event)
    assert "error=stress exceeded allowable" in text


def test_event_to_text_includes_duration_when_numeric():
    event = _event(
        EventType.AGENT_TASK_COMPLETED,
        {"agent_code": "sim", "duration": 12.5, "result": "ok"},
    )
    text = event_to_text(event)
    assert "duration=12.500s" in text


def test_event_to_text_omits_task_type_when_missing():
    event = _event(
        EventType.AGENT_TASK_STARTED,
        {"agent_code": "mech", "run_id": "r", "step_id": "s"},
    )
    text = event_to_text(event)
    assert "task_type=" not in text
