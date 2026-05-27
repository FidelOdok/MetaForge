"""Confidence decay for consolidated insights (MET-455 Phase 3).

Insights aren't permanent truths — a synthesized lesson from
three-month-old runs deserves less weight than one from yesterday. This
module applies time-based exponential decay to an insight's confidence
so the JANITOR consolidation pass can re-validate against a *decayed*
confidence and flag insights that have faded below the validator's
threshold (the "active forgetting" the architecture calls for).

Decay is reversible: re-synthesizing the same lesson stamps a fresh
``synthesized_at``, which resets the clock. This module only computes
the decayed value; the orchestrator decides what to do with it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from digital_twin.memory.consolidation.insight import Insight

DEFAULT_HALF_LIFE_DAYS = 90.0
"""Per the spec: insights fade over ~90 days unless reinforced. After
one half-life the confidence contribution from age is halved."""


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
