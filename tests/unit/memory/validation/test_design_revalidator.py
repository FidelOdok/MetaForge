"""Unit tests for ``DesignRevalidator`` (MET-455 Phase 3)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from digital_twin.memory.validation.design_revalidator import (
    DesignRevalidationResult,
    DesignRevalidator,
)
from twin_core.constraint_engine.models import (
    ConstraintEvaluationResult,
    ConstraintViolation,
)
from twin_core.models.enums import ConstraintSeverity


class _FakeConstraintEngine:
    """Per-design evaluate() driven by a {design_id: passed} map."""

    def __init__(self, verdicts: dict[UUID, bool]) -> None:
        self._verdicts = verdicts
        self.evaluated: list[list[UUID]] = []

    async def evaluate(self, work_product_ids: list[UUID]) -> ConstraintEvaluationResult:
        self.evaluated.append(list(work_product_ids))
        target = work_product_ids[0]
        passed = self._verdicts.get(target, True)
        violations: list[ConstraintViolation] = []
        if not passed:
            violations.append(
                ConstraintViolation(
                    constraint_id=uuid4(),
                    constraint_name="max_current",
                    severity=ConstraintSeverity.ERROR,
                    message="draws 2.1A, limit 1.5A",
                    work_product_ids=[target],
                    expression="ctx.current <= 1.5",
                    evaluated_at=datetime.now(UTC),
                )
            )
        return ConstraintEvaluationResult(
            passed=passed,
            violations=violations,
            evaluated_count=1,
        )


@pytest.mark.asyncio
async def test_empty_input_is_noop():
    engine = _FakeConstraintEngine({})
    result = await DesignRevalidator(engine).revalidate([])
    assert result == DesignRevalidationResult()
    assert result.passed is True
    assert engine.evaluated == []


@pytest.mark.asyncio
async def test_all_passing_designs_report_no_violations():
    d1, d2 = uuid4(), uuid4()
    engine = _FakeConstraintEngine({d1: True, d2: True})
    result = await DesignRevalidator(engine).revalidate([d1, d2])
    assert result.revalidated_count == 2
    assert result.violated_count == 0
    assert result.passed is True


@pytest.mark.asyncio
async def test_violated_design_is_surfaced_with_summaries():
    good, bad = uuid4(), uuid4()
    engine = _FakeConstraintEngine({good: True, bad: False})
    result = await DesignRevalidator(engine).revalidate([good, bad])

    assert result.revalidated_count == 2
    assert result.violated_count == 1
    assert result.passed is False
    assert result.violated_design_ids == (bad,)
    violation = result.violated[0]
    assert violation.design_id == bad
    assert violation.violation_summaries == ("max_current: draws 2.1A, limit 1.5A",)


@pytest.mark.asyncio
async def test_each_design_evaluated_individually():
    d1, d2, d3 = uuid4(), uuid4(), uuid4()
    engine = _FakeConstraintEngine({d1: True, d2: False, d3: True})
    await DesignRevalidator(engine).revalidate([d1, d2, d3])
    assert engine.evaluated == [[d1], [d2], [d3]]


@pytest.mark.asyncio
async def test_duplicate_design_ids_deduped():
    d1 = uuid4()
    engine = _FakeConstraintEngine({d1: False})
    result = await DesignRevalidator(engine).revalidate([d1, d1, d1])
    assert result.revalidated_count == 1
    assert engine.evaluated == [[d1]]
