"""Unit tests for ``digital_twin.memory.consolidation.modes``."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from digital_twin.memory.consolidation.modes import (
    ON_DEMAND_IMPORTANCE_FLOOR,
    ConsolidationMode,
    ConsolidationModeError,
    ConsolidationRunRequest,
)
from digital_twin.memory.consolidation.themes import ConsolidationTheme


def test_background_request_has_no_required_extras():
    req = ConsolidationRunRequest()
    assert req.mode == ConsolidationMode.BACKGROUND
    assert req.effective_min_importance is None


def test_proactive_request_requires_project_id():
    with pytest.raises(ConsolidationModeError, match="project_id"):
        ConsolidationRunRequest(mode=ConsolidationMode.PROACTIVE)


def test_proactive_with_project_id_constructs():
    project_id = UUID("00000000-0000-0000-0000-000000000001")
    req = ConsolidationRunRequest(mode=ConsolidationMode.PROACTIVE, project_id=project_id)
    assert req.project_id == project_id


def test_on_demand_pins_importance_floor_to_zero():
    req = ConsolidationRunRequest(mode=ConsolidationMode.ON_DEMAND)
    assert req.effective_min_importance == ON_DEMAND_IMPORTANCE_FLOOR


def test_explicit_min_importance_overrides_mode_default():
    req = ConsolidationRunRequest(mode=ConsolidationMode.ON_DEMAND, min_importance=0.55)
    assert req.effective_min_importance == 0.55


def test_window_validation_rejects_inverted_range():
    base = datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC)
    with pytest.raises(ConsolidationModeError, match="since must be <= until"):
        ConsolidationRunRequest(since=base + timedelta(hours=2), until=base)


def test_proactive_can_carry_a_theme():
    req = ConsolidationRunRequest(
        mode=ConsolidationMode.PROACTIVE,
        project_id=uuid4(),
        theme=ConsolidationTheme.POWER_ANALYSIS,
    )
    assert req.theme == ConsolidationTheme.POWER_ANALYSIS


def test_janitor_request_does_not_need_project_id():
    req = ConsolidationRunRequest(mode=ConsolidationMode.JANITOR)
    assert req.mode == ConsolidationMode.JANITOR
