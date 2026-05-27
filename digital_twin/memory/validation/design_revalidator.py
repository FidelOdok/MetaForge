"""Re-validate designs after a spec change (MET-455 Phase 3).

When L4 detects that a datasheet revision superseded an old one, two
things go stale: the *insights* synthesized from experiences that cited
the old spec (handled by ``StalenessInvalidator``), and the *designs*
that were validated against it. This module is the design half — it
re-runs the constraint engine over the affected work products and
reports which ones now violate an ERROR-severity constraint.

The revalidator depends only on a structural ``SupportsConstraintEvaluation``
protocol (anything exposing ``async evaluate(ids) -> ConstraintEvaluationResult``),
so the real ``twin_core`` constraint engine, its Neo4j variant, or a
test double all satisfy it without a hard coupling. Designs that have
started failing are surfaced via ``DesignRevalidationResult.violated``
for the escalation workflow to act on; passing designs are not touched.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

import structlog

from observability.tracing import get_tracer
from twin_core.constraint_engine.models import (
    ConstraintEvaluationResult,
    ConstraintViolation,
)

logger = structlog.get_logger(__name__)
tracer = get_tracer("digital_twin.memory.validation.design_revalidator")


class SupportsConstraintEvaluation(Protocol):
    """Minimal slice of the constraint engine the revalidator needs."""

    async def evaluate(self, work_product_ids: list[UUID]) -> ConstraintEvaluationResult: ...


@dataclass(frozen=True)
class DesignViolation:
    """One design that failed revalidation, with its blocking violations."""

    design_id: UUID
    violations: tuple[ConstraintViolation, ...] = field(default_factory=tuple)

    @property
    def violation_summaries(self) -> tuple[str, ...]:
        """Human-readable ``name: message`` strings for escalation copy."""
        return tuple(f"{v.constraint_name}: {v.message}" for v in self.violations)


@dataclass(frozen=True)
class DesignRevalidationResult:
    """Outcome of revalidating a set of designs after a spec change."""

    revalidated_design_ids: tuple[UUID, ...] = field(default_factory=tuple)
    violated: tuple[DesignViolation, ...] = field(default_factory=tuple)

    @property
    def revalidated_count(self) -> int:
        return len(self.revalidated_design_ids)

    @property
    def violated_count(self) -> int:
        return len(self.violated)

    @property
    def passed(self) -> bool:
        """True when no revalidated design violates an ERROR constraint."""
        return not self.violated

    @property
    def violated_design_ids(self) -> tuple[UUID, ...]:
        return tuple(v.design_id for v in self.violated)


class DesignRevalidator:
    """Re-run constraint validation over designs touched by a spec change."""

    def __init__(self, constraint_engine: SupportsConstraintEvaluation) -> None:
        self._engine = constraint_engine

    async def revalidate(
        self,
        design_ids: set[UUID] | list[UUID],
    ) -> DesignRevalidationResult:
        """Re-evaluate each design individually; report those now violating.

        Designs are evaluated one at a time so the result attributes a
        clear set of blocking violations to each failing design (an
        aggregate pass over all ids would blur which design owns which
        violation). A design "fails" when its evaluation returns
        ``passed is False`` — i.e. at least one ERROR-severity constraint
        violated. Empty input is a no-op.
        """
        ids = list(dict.fromkeys(design_ids))  # de-dupe, preserve order
        if not ids:
            return DesignRevalidationResult()

        with tracer.start_as_current_span("design_revalidator.revalidate") as span:
            span.set_attribute("memory.revalidate_input", len(ids))
            violated: list[DesignViolation] = []
            for design_id in ids:
                result = await self._engine.evaluate([design_id])
                if not result.passed:
                    violated.append(
                        DesignViolation(
                            design_id=design_id,
                            violations=tuple(result.violations),
                        )
                    )

            outcome = DesignRevalidationResult(
                revalidated_design_ids=tuple(ids),
                violated=tuple(violated),
            )
            span.set_attribute("memory.revalidated", outcome.revalidated_count)
            span.set_attribute("memory.violated", outcome.violated_count)
            logger.info(
                "design_revalidation_completed",
                revalidated=outcome.revalidated_count,
                violated=outcome.violated_count,
            )
            return outcome
