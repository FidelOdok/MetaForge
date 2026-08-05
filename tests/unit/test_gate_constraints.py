"""TwinConstraintChecker: constraint-engine state at design-flow gates (MET-583).

Network-free — fake twin + project backend. Covers project scoping (violations
citing the project's work products count; foreign ones don't; global ones
always do) and the fail-open contract when the twin can't evaluate.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from api_gateway.runs.gate_eval import TwinConstraintChecker
from twin_core.constraint_engine.models import (
    ConstraintEvaluationResult,
    ConstraintViolation,
)
from twin_core.models.enums import ConstraintSeverity


def _violation(name: str, wp_ids: list | None = None, sev: str = "error") -> ConstraintViolation:
    return ConstraintViolation(
        constraint_id=uuid4(),
        constraint_name=name,
        severity=ConstraintSeverity(sev),
        message=f"{name} exceeded",
        work_product_ids=wp_ids or [],
        expression="x <= limit",
        evaluated_at=datetime.now(UTC),
    )


class _FakeTwin:
    def __init__(self, result: ConstraintEvaluationResult) -> None:
        self._result = result

    async def evaluate_constraints(self, branch: str = "main") -> ConstraintEvaluationResult:
        return self._result


class _FakeBackend:
    def __init__(self, wp_ids: list | None) -> None:
        self._wp_ids = wp_ids

    async def get_project(self, project_id: str) -> object | None:
        if self._wp_ids is None:
            return None
        return SimpleNamespace(work_products=[SimpleNamespace(id=w) for w in self._wp_ids])


@pytest.mark.asyncio
async def test_violations_scoped_to_project_work_products() -> None:
    mine, foreign = uuid4(), uuid4()
    result = ConstraintEvaluationResult(
        passed=False,
        violations=[_violation("mass_budget", [mine]), _violation("other_project", [foreign])],
        warnings=[_violation("cost_target", [mine], sev="warning")],
        evaluated_count=3,
    )
    checker = TwinConstraintChecker(_FakeTwin(result), _FakeBackend([mine]))
    report = await checker.check("p1")
    assert report.checked and not report.passed
    assert report.violations == ["mass_budget: mass_budget exceeded"]
    assert report.warnings == ["cost_target: cost_target exceeded"]
    assert report.evaluated_count == 3


@pytest.mark.asyncio
async def test_global_violations_always_count() -> None:
    result = ConstraintEvaluationResult(
        passed=False, violations=[_violation("global_rule", [])], evaluated_count=1
    )
    checker = TwinConstraintChecker(_FakeTwin(result), _FakeBackend([uuid4()]))
    report = await checker.check("p1")
    assert report.violations and "global_rule" in report.violations[0]


@pytest.mark.asyncio
async def test_unscopable_project_counts_everything() -> None:
    """Missing project / backend -> can't scope, so nothing is filtered out."""
    result = ConstraintEvaluationResult(
        passed=False, violations=[_violation("v", [uuid4()])], evaluated_count=1
    )
    for backend in (None, _FakeBackend(None)):
        report = await TwinConstraintChecker(_FakeTwin(result), backend).check("p1")
        assert len(report.violations) == 1


@pytest.mark.asyncio
async def test_twin_without_engine_reads_unchecked() -> None:
    report = await TwinConstraintChecker(object(), None).check("p1")
    assert not report.checked and report.passed


@pytest.mark.asyncio
async def test_clean_evaluation_passes() -> None:
    result = ConstraintEvaluationResult(passed=True, evaluated_count=4)
    report = await TwinConstraintChecker(_FakeTwin(result), None).check("p1")
    assert report.checked and report.passed and report.evaluated_count == 4
