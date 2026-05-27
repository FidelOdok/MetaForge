"""Staleness invalidation — the memory-side of L4-triggered invalidation (MET-455).

When L4 detects that a datasheet revision superseded an old one, the
experiences (and the insights synthesized from them) that relied on the
stale spec should no longer be trusted. L4 supplies the set of affected
experience IDs; this module is the half that acts on them: it scans the
insight store, finds every insight citing one of those experiences via
``supporting_experience_ids``, and marks it ``STALE_WARN``.

The L4 event subscription that *produces* the invalidated-id set lives
in the L4 datasheet-change infrastructure (cross-module) — this engine
is the deterministic, unit-testable consumer of that signal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

import structlog

from digital_twin.memory.consolidation.insight import InsightStatus
from digital_twin.memory.consolidation.themes import ConsolidationTheme
from digital_twin.memory.consolidation.writer import InsightStore
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("digital_twin.memory.consolidation.staleness")


@dataclass(frozen=True)
class InvalidationResult:
    """Outcome of one invalidation pass."""

    scanned_count: int = 0
    invalidated_count: int = 0
    invalidated_insight_ids: tuple[UUID, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class RestorationResult:
    """Outcome of one rollback / restoration pass."""

    scanned_count: int = 0
    restored_count: int = 0
    restored_insight_ids: tuple[UUID, ...] = field(default_factory=tuple)


class StalenessInvalidator:
    """Mark insights stale when their supporting experiences are invalidated."""

    def __init__(self, store: InsightStore) -> None:
        self._store = store

    async def invalidate_by_experiences(
        self,
        invalidated_experience_ids: set[UUID],
        *,
        theme: ConsolidationTheme | None = None,
        limit: int = 10_000,
    ) -> InvalidationResult:
        """Mark every insight citing an invalidated experience as ``STALE_WARN``.

        An insight is invalidated when *any* of its
        ``supporting_experience_ids`` is in ``invalidated_experience_ids``.
        Already-STALE_WARN insights are skipped (no redundant write).
        ``theme`` optionally narrows the scan. Empty input is a no-op.
        """
        if not invalidated_experience_ids:
            return InvalidationResult()

        with tracer.start_as_current_span("staleness.invalidate_by_experiences") as span:
            span.set_attribute("memory.invalidated_input", len(invalidated_experience_ids))
            existing = await self._store.list(theme=theme, limit=limit)
            invalidated_ids: list[UUID] = []
            for insight in existing:
                if insight.status is InsightStatus.STALE_WARN:
                    continue
                if invalidated_experience_ids.intersection(insight.supporting_experience_ids):
                    await self._store.write(
                        insight.model_copy(update={"status": InsightStatus.STALE_WARN})
                    )
                    invalidated_ids.append(insight.id)

            result = InvalidationResult(
                scanned_count=len(existing),
                invalidated_count=len(invalidated_ids),
                invalidated_insight_ids=tuple(invalidated_ids),
            )
            span.set_attribute("memory.scanned", result.scanned_count)
            span.set_attribute("memory.invalidated", result.invalidated_count)
            logger.info(
                "staleness_invalidation_completed",
                scanned=result.scanned_count,
                invalidated=result.invalidated_count,
                theme=theme.value if theme else None,
            )
            return result

    async def restore_by_experiences(
        self,
        restored_experience_ids: set[UUID],
        *,
        theme: ConsolidationTheme | None = None,
        limit: int = 10_000,
    ) -> RestorationResult:
        """Flip ``STALE_WARN`` insights back to ``ACTIVE`` when a spec is reverted.

        The rollback counterpart to ``invalidate_by_experiences``: when L4
        reports that a previously-superseded datasheet revision has been
        restored (spec reverted), the experiences that relied on it are
        trustworthy again, so any insight that was marked ``STALE_WARN``
        *solely* for citing one of those experiences should be reinstated.

        An insight is restored when it is currently ``STALE_WARN`` and
        *any* of its ``supporting_experience_ids`` is in
        ``restored_experience_ids``. Already-``ACTIVE`` insights are
        skipped. ``theme`` optionally narrows the scan. Empty input is a
        no-op.
        """
        if not restored_experience_ids:
            return RestorationResult()

        with tracer.start_as_current_span("staleness.restore_by_experiences") as span:
            span.set_attribute("memory.restored_input", len(restored_experience_ids))
            existing = await self._store.list(theme=theme, limit=limit)
            restored_ids: list[UUID] = []
            for insight in existing:
                if insight.status is not InsightStatus.STALE_WARN:
                    continue
                if restored_experience_ids.intersection(insight.supporting_experience_ids):
                    await self._store.write(
                        insight.model_copy(update={"status": InsightStatus.ACTIVE})
                    )
                    restored_ids.append(insight.id)

            result = RestorationResult(
                scanned_count=len(existing),
                restored_count=len(restored_ids),
                restored_insight_ids=tuple(restored_ids),
            )
            span.set_attribute("memory.scanned", result.scanned_count)
            span.set_attribute("memory.restored", result.restored_count)
            logger.info(
                "staleness_restoration_completed",
                scanned=result.scanned_count,
                restored=result.restored_count,
                theme=theme.value if theme else None,
            )
            return result
