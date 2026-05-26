"""Unit tests for ``digital_twin.memory.consolidation.grouper``."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from digital_twin.memory.consolidation.grouper import EventGrouper, ExperienceGroup
from digital_twin.memory.consolidation.themes import ConsolidationTheme
from digital_twin.memory.models import ConfidenceTier, ExperienceMemory


def _exp(
    *,
    task_type: str = "",
    agent_code: str = "",
    success: bool = True,
) -> ExperienceMemory:
    return ExperienceMemory(
        id=uuid4(),
        run_id="r",
        step_id="s",
        agent_code=agent_code,
        task_type=task_type,
        success=success,
        result_summary="",
        timestamp=datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC),
        importance=0.5,
        confidence=ConfidenceTier.VERBATIM,
    )


def test_empty_input_returns_empty_list():
    assert EventGrouper().group([]) == []


def test_groups_by_theme_with_default_min_size():
    grouper = EventGrouper()
    experiences = [
        _exp(task_type="stress_check"),
        _exp(task_type="stress_check"),
        _exp(task_type="run_erc"),
        _exp(task_type="run_drc"),
    ]
    groups = grouper.group(experiences)
    themes = {g.theme for g in groups}
    assert ConsolidationTheme.MECHANICAL_VALIDATION in themes
    assert ConsolidationTheme.CIRCUIT_DESIGN_RULE in themes
    for group in groups:
        assert isinstance(group, ExperienceGroup)
        assert group.size >= 1


def test_singletons_below_min_size_roll_into_misc():
    grouper = EventGrouper(min_group_size=2)
    experiences = [
        _exp(task_type="stress_check"),
        _exp(task_type="stress_check"),
        _exp(task_type="run_erc"),  # singleton
        _exp(task_type="power_budget"),  # singleton
    ]
    groups = grouper.group(experiences)
    misc = next(g for g in groups if g.theme == ConsolidationTheme.MISC)
    assert misc.size == 2


def test_singletons_merged_with_existing_misc_bucket():
    grouper = EventGrouper(min_group_size=2)
    experiences = [
        _exp(task_type="stress_check"),
        _exp(task_type="stress_check"),
        _exp(agent_code="unknown"),  # naturally MISC
        _exp(task_type="run_erc"),  # rolled into MISC due to size
    ]
    groups = grouper.group(experiences)
    misc = next(g for g in groups if g.theme == ConsolidationTheme.MISC)
    assert misc.size == 2


def test_groups_sorted_by_descending_size():
    grouper = EventGrouper(min_group_size=1)
    experiences = [
        _exp(task_type="stress_check"),
        _exp(task_type="run_erc"),
        _exp(task_type="run_erc"),
        _exp(task_type="run_erc"),
    ]
    groups = grouper.group(experiences)
    assert groups[0].theme == ConsolidationTheme.CIRCUIT_DESIGN_RULE
    assert groups[0].size == 3
    assert groups[1].theme == ConsolidationTheme.MECHANICAL_VALIDATION


def test_failure_and_success_counts():
    group = ExperienceGroup(
        theme=ConsolidationTheme.MECHANICAL_VALIDATION,
        experiences=(
            _exp(success=True),
            _exp(success=False),
            _exp(success=False),
        ),
    )
    assert group.success_count == 1
    assert group.failure_count == 2


def test_invalid_min_group_size_rejected():
    with pytest.raises(ValueError, match=">= 1"):
        EventGrouper(min_group_size=0)
