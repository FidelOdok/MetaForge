"""Unit tests for ``digital_twin.memory.consolidation.themes``."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from digital_twin.memory.consolidation.themes import (
    ConsolidationTheme,
    classify_theme,
)
from digital_twin.memory.models import ConfidenceTier, ExperienceMemory


def _exp(*, agent_code: str = "", task_type: str = "") -> ExperienceMemory:
    return ExperienceMemory(
        id=uuid4(),
        run_id="r",
        step_id="s",
        agent_code=agent_code,
        task_type=task_type,
        success=True,
        result_summary="",
        timestamp=datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC),
        importance=0.5,
        confidence=ConfidenceTier.VERBATIM,
    )


def test_task_type_keyword_wins_over_agent_code():
    # Even though agent_code is "mechanical", the task_type signals a power_analysis pass.
    exp = _exp(agent_code="mechanical", task_type="power_budget_check")
    assert classify_theme(exp) == ConsolidationTheme.POWER_ANALYSIS


def test_stress_keyword_routes_to_mechanical_validation():
    exp = _exp(task_type="stress_validation")
    assert classify_theme(exp) == ConsolidationTheme.MECHANICAL_VALIDATION


def test_erc_keyword_routes_to_circuit_design_rule():
    exp = _exp(task_type="run_erc")
    assert classify_theme(exp) == ConsolidationTheme.CIRCUIT_DESIGN_RULE


def test_agent_code_fallback_when_task_type_empty():
    exp = _exp(agent_code="firmware")
    assert classify_theme(exp) == ConsolidationTheme.FIRMWARE_BUILD


def test_unknown_agent_falls_back_to_misc():
    exp = _exp(agent_code="unknown_agent")
    assert classify_theme(exp) == ConsolidationTheme.MISC


def test_completely_empty_experience_lands_in_misc():
    exp = _exp()
    assert classify_theme(exp) == ConsolidationTheme.MISC


def test_case_insensitive_matching():
    exp = _exp(task_type="STRESS_CHECK", agent_code="MECHANICAL")
    assert classify_theme(exp) == ConsolidationTheme.MECHANICAL_VALIDATION
