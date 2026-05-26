"""Unit tests for ``digital_twin.memory.importance``."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from digital_twin.memory.importance import (
    DEFAULT_RECENCY_HALF_LIFE_HOURS,
    ImportanceWeights,
    score_importance,
)
from orchestrator.event_bus.events import Event, EventType


def _event(
    event_type: EventType,
    *,
    timestamp: datetime,
    data: dict | None = None,
) -> Event:
    return Event(
        id="evt-1",
        type=event_type,
        timestamp=timestamp.isoformat(),
        source="scheduler",
        data=data or {},
    )


def test_failed_events_score_higher_than_started():
    now = datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC)
    started = _event(
        EventType.AGENT_TASK_STARTED,
        timestamp=now,
        data={"agent_code": "mech", "run_id": "r", "step_id": "s"},
    )
    failed = _event(
        EventType.AGENT_TASK_FAILED,
        timestamp=now,
        data={
            "agent_code": "mech",
            "run_id": "r",
            "step_id": "s",
            "error": "stress validation exceeded allowable",
        },
    )
    started_score = score_importance(started, now=now)
    failed_score = score_importance(failed, now=now)
    assert failed_score.total > started_score.total
    assert failed_score.criticality == 1.0
    assert started_score.criticality == pytest.approx(0.2)


def test_recency_decays_with_half_life():
    now = datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC)
    one_half_life_old = now - timedelta(hours=DEFAULT_RECENCY_HALF_LIFE_HOURS)
    recent = _event(
        EventType.AGENT_TASK_COMPLETED,
        timestamp=now,
        data={"agent_code": "elec", "result": {"status": "ok"}},
    )
    stale = _event(
        EventType.AGENT_TASK_COMPLETED,
        timestamp=one_half_life_old,
        data={"agent_code": "elec", "result": {"status": "ok"}},
    )
    recent_score = score_importance(recent, now=now)
    stale_score = score_importance(stale, now=now)
    assert recent_score.recency == pytest.approx(1.0)
    assert stale_score.recency == pytest.approx(0.5, abs=1e-6)
    assert recent_score.total > stale_score.total


def test_unparseable_timestamp_does_not_crash():
    event = Event(
        id="evt-2",
        type=EventType.AGENT_TASK_COMPLETED,
        timestamp="not-an-iso-date",
        source="scheduler",
        data={"agent_code": "fw", "result": {"status": "ok"}},
    )
    score = score_importance(event)
    assert score.recency == 0.5
    assert 0.0 <= score.total <= 1.0


def test_weights_must_sum_to_one():
    with pytest.raises(ValueError, match="must sum to 1.0"):
        ImportanceWeights(recency=0.1, relevance=0.1, criticality=0.1)


def test_total_is_clamped_to_unit_interval():
    now = datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC)
    event = _event(
        EventType.AGENT_TASK_FAILED,
        timestamp=now,
        data={
            "agent_code": "mech",
            "run_id": "r",
            "step_id": "long-identifier-for-text-bonus",
            "error": "stress exceeded",
            "result": {"status": "fail"},
            "task_type": "validate_stress",
            "duration": 1.5,
        },
    )
    score = score_importance(event, now=now)
    assert 0.0 <= score.total <= 1.0


def test_custom_weights_change_total():
    now = datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC)
    event = _event(
        EventType.AGENT_TASK_FAILED,
        timestamp=now,
        data={"agent_code": "mech", "error": "boom"},
    )
    default = score_importance(event, now=now)
    criticality_only = score_importance(
        event,
        now=now,
        weights=ImportanceWeights(recency=0.0, relevance=0.0, criticality=1.0),
    )
    assert criticality_only.total == pytest.approx(1.0)
    assert default.total < criticality_only.total
