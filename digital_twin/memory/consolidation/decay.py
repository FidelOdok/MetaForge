"""Confidence decay for consolidated insights (MET-455 Phase 3 + MET-472).

Insights aren't permanent truths — a synthesized lesson from
three-month-old runs deserves less weight than one from yesterday. This
module applies time-based exponential decay to an insight's confidence
so the JANITOR consolidation pass can re-validate against a *decayed*
confidence and flag insights that have faded below the validator's
threshold (the "active forgetting" the architecture calls for).

Decay is reversible: re-synthesizing the same lesson stamps a fresh
``synthesized_at``, which resets the clock. This module computes the
decayed value (:class:`ConfidenceDecay`) and exposes the daily-tick
sweep that persists decayed insights and flips status to
``STALE_WARN`` once they drop below the stale threshold
(:func:`decay_insights_in_store` — MET-472).
"""

from __future__ import annotations

import time as _time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog

from digital_twin.memory.consolidation.insight import Insight, InsightStatus
from observability.tracing import get_tracer

if TYPE_CHECKING:
    from digital_twin.memory.consolidation.themes import ConsolidationTheme
    from digital_twin.memory.consolidation.writer import InsightStore

logger = structlog.get_logger(__name__)
tracer = get_tracer("digital_twin.memory.consolidation.decay")

DEFAULT_HALF_LIFE_DAYS = 90.0
"""Per the spec: insights fade over ~90 days unless reinforced. After
one half-life the confidence contribution from age is halved."""

DEFAULT_STALE_THRESHOLD = 0.70
"""MET-472: confidence at or below which an insight is flagged
``STALE_WARN``. The validator gates new insights on >= 0.70, so the
same value is the natural staleness floor."""


@dataclass(frozen=True)
class ConfidenceDecay:
    """Exponential time-decay parameters for insight confidence."""

    half_life_days: float = DEFAULT_HALF_LIFE_DAYS
    floor: float = 0.0
    """Decayed confidence never drops below this — set > 0 to keep a
    residual signal for very old insights."""

    def __post_init__(self) -> None:
        if self.half_life_days <= 0:
            raise ValueError(f"half_life_days must be > 0, got {self.half_life_days!r}")
        if not 0.0 <= self.floor <= 1.0:
            raise ValueError(f"floor must be in [0, 1], got {self.floor!r}")

    def factor(self, age_days: float) -> float:
        """Multiplicative decay factor in ``[0, 1]`` for the given age."""
        if age_days <= 0:
            return 1.0
        return float(0.5 ** (age_days / self.half_life_days))

    def decayed_confidence(
        self,
        insight: Insight,
        *,
        now: datetime | None = None,
    ) -> float:
        """Return ``insight.confidence`` decayed by its age.

        Age is measured from ``insight.synthesized_at`` to ``now``
        (defaults to the current UTC time). The result is clamped to
        ``[floor, original_confidence]`` so decay only ever reduces
        confidence, never inflates it, and never crosses the floor.
        """
        reference = now or datetime.now(UTC)
        synthesized = insight.synthesized_at
        if synthesized.tzinfo is None:
            synthesized = synthesized.replace(tzinfo=UTC)
        age_seconds = max(0.0, (reference - synthesized).total_seconds())
        age_days = age_seconds / 86400.0
        decayed = insight.confidence * self.factor(age_days)
        return max(self.floor, min(insight.confidence, decayed))

    def with_decayed_confidence(
        self,
        insight: Insight,
        *,
        now: datetime | None = None,
    ) -> Insight:
        """Return a copy of ``insight`` with ``confidence`` replaced by its decayed value.

        Useful for feeding a time-adjusted insight back through the
        ``InsightValidator`` (e.g. in a JANITOR pass) without mutating
        the stored record.
        """
        decayed = self.decayed_confidence(insight, now=now)
        return insight.model_copy(update={"confidence": decayed})


def is_stale(
    insight: Insight,
    *,
    threshold: float,
    decay: ConfidenceDecay | None = None,
    now: datetime | None = None,
) -> bool:
    """True when the insight's *decayed* confidence has dropped below ``threshold``.

    The canonical "should this insight be re-evaluated?" check. Uses the
    default 90-day half-life unless a custom ``ConfidenceDecay`` is
    supplied.
    """
    decay = decay or ConfidenceDecay()
    return decay.decayed_confidence(insight, now=now) < threshold


# ---------------------------------------------------------------------------
# MET-472: daily-tick sweep that persists decayed confidence + status
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DecayOutcome:
    """Per-insight result of one decay tick (MET-472)."""

    insight: Insight
    """The (possibly) updated insight. ``confidence`` and ``status`` are
    the only fields that change; the input is never mutated."""
    confidence_before: float
    confidence_after: float
    status_changed: bool


@dataclass(frozen=True)
class DecaySweepResult:
    """Aggregate result from :func:`decay_insights_in_store` (MET-472)."""

    scanned: int
    """Insights returned by the store."""
    updated: int
    """How many had their confidence or status persisted."""
    newly_stale: int
    """How many transitioned ACTIVE → STALE_WARN on this tick."""
    elapsed_ms: float


def apply_decay_to_insight(
    insight: Insight,
    *,
    decay: ConfidenceDecay | None = None,
    stale_threshold: float = DEFAULT_STALE_THRESHOLD,
    now: datetime | None = None,
) -> DecayOutcome:
    """Return a copy of ``insight`` with decayed confidence + maybe stale status.

    Pure function — no I/O. ``ConfidenceDecay.with_decayed_confidence``
    computes the new value; this wrapper additionally flips ``status``
    to ``STALE_WARN`` when the result is at or below the threshold.
    Pure decay never *un-stales* — that escape hatch belongs to the
    consolidation orchestrator's re-validation pass which restamps
    ``synthesized_at`` and writes a fresh insight back.
    """
    if not 0.0 <= stale_threshold <= 1.0:
        raise ValueError(f"stale_threshold must be in [0, 1], got {stale_threshold!r}")
    decay = decay or ConfidenceDecay()
    new_conf = decay.decayed_confidence(insight, now=now)
    new_status = InsightStatus.STALE_WARN if new_conf <= stale_threshold else insight.status
    updated = insight.model_copy(update={"confidence": new_conf, "status": new_status})
    return DecayOutcome(
        insight=updated,
        confidence_before=insight.confidence,
        confidence_after=new_conf,
        status_changed=new_status != insight.status,
    )


async def decay_insights_in_store(
    store: InsightStore,
    *,
    decay: ConfidenceDecay | None = None,
    stale_threshold: float = DEFAULT_STALE_THRESHOLD,
    theme: ConsolidationTheme | None = None,
    page_size: int = 200,
    now: datetime | None = None,
    persist_epsilon: float = 1e-4,
) -> DecaySweepResult:
    """Walk every insight in ``store`` and persist any that changed (MET-472).

    The daily-tick entry point. ``persist_epsilon`` is the minimum
    confidence delta required to trigger a write — keeps us from
    re-writing rows for sub-noise floating-point jitter on every tick.
    Returns a :class:`DecaySweepResult` for dashboards / alerts.

    Both ``PgVectorInsightStore`` and ``Neo4jInsightStore`` UPSERT on
    ``id``, so ``store.write(updated)`` is the natural update path.
    """
    sweep_start = _time.monotonic()
    decay = decay or ConfidenceDecay()
    scanned = 0
    updated = 0
    newly_stale = 0

    with tracer.start_as_current_span("insight_decay.sweep") as span:
        span.set_attribute("decay.half_life_days", decay.half_life_days)
        span.set_attribute("decay.stale_threshold", stale_threshold)
        span.set_attribute("decay.theme", theme.value if theme is not None else "*")

        insights = await store.list(theme=theme, limit=page_size)
        for insight in insights:
            scanned += 1
            outcome = apply_decay_to_insight(
                insight,
                decay=decay,
                stale_threshold=stale_threshold,
                now=now,
            )
            if (
                abs(outcome.confidence_before - outcome.confidence_after) < persist_epsilon
                and not outcome.status_changed
            ):
                continue
            try:
                await store.write(outcome.insight)
            except Exception as exc:  # noqa: BLE001 — recoverable; keep sweeping
                span.record_exception(exc)
                logger.warning(
                    "insight_decay_write_failed",
                    insight_id=str(insight.id),
                    error=str(exc),
                )
                continue
            updated += 1
            if outcome.status_changed:
                newly_stale += 1

        elapsed_ms = (_time.monotonic() - sweep_start) * 1000.0
        span.set_attribute("decay.scanned", scanned)
        span.set_attribute("decay.updated", updated)
        span.set_attribute("decay.newly_stale", newly_stale)
        logger.info(
            "insight_decay_sweep_completed",
            scanned=scanned,
            updated=updated,
            newly_stale=newly_stale,
            elapsed_ms=round(elapsed_ms, 2),
            half_life_days=decay.half_life_days,
            stale_threshold=stale_threshold,
        )
        return DecaySweepResult(
            scanned=scanned,
            updated=updated,
            newly_stale=newly_stale,
            elapsed_ms=round(elapsed_ms, 2),
        )
