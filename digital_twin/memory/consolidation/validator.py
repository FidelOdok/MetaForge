"""Stage 4 of the consolidation pipeline — validate synthesized insights.

Gates an ``Insight`` against three orthogonal checks before the writer
persists it:

1. **Confidence threshold** — spec-pinned at 0.70. Anything below is
   dropped (the LLM didn't believe its own answer).
2. **Hallucination heuristic** — narrative length sanity (too short =
   filler) and "I don't know" patterns the model sometimes emits.
3. **Provenance** — at least one supporting experience must be cited
   so downstream readers can audit the source.

Returns a ``ValidationResult`` rather than raising, so the orchestrator
can log + skip a single bad insight without aborting the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from digital_twin.memory.consolidation.insight import Insight

logger = structlog.get_logger(__name__)


DEFAULT_MIN_CONFIDENCE = 0.70
DEFAULT_MIN_NARRATIVE_LENGTH = 32
"""Below this many characters the narrative is almost certainly filler."""

_HALLUCINATION_PHRASES: tuple[str, ...] = (
    "i don't know",
    "i do not know",
    "insufficient data",
    "not enough information",
    "n/a",
    "no pattern",
)


@dataclass(frozen=True)
class ValidationResult:
    """Verdict + structured reason."""

    accepted: bool
    reason: str = ""

    @classmethod
    def accept(cls) -> ValidationResult:
        return cls(accepted=True)

    @classmethod
    def reject(cls, reason: str) -> ValidationResult:
        return cls(accepted=False, reason=reason)


class InsightValidator:
    """Apply the spec's validation gates to a candidate insight."""

    def __init__(
        self,
        *,
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
        min_narrative_length: int = DEFAULT_MIN_NARRATIVE_LENGTH,
    ) -> None:
        if not 0.0 <= min_confidence <= 1.0:
            raise ValueError(
                f"min_confidence must be in [0, 1], got {min_confidence!r}"
            )
        if min_narrative_length < 1:
            raise ValueError(
                f"min_narrative_length must be >= 1, got {min_narrative_length!r}"
            )
        self._min_confidence = min_confidence
        self._min_narrative_length = min_narrative_length

    def validate(self, insight: Insight) -> ValidationResult:
        if insight.confidence < self._min_confidence:
            return ValidationResult.reject(
                f"confidence {insight.confidence:.2f} < threshold {self._min_confidence:.2f}"
            )

        narrative = insight.narrative.strip()
        if len(narrative) < self._min_narrative_length:
            return ValidationResult.reject(
                f"narrative too short ({len(narrative)} < {self._min_narrative_length})"
            )

        lowered = narrative.lower()
        for phrase in _HALLUCINATION_PHRASES:
            if phrase in lowered:
                return ValidationResult.reject(
                    f"narrative matches hallucination phrase: {phrase!r}"
                )

        if not insight.supporting_experience_ids:
            return ValidationResult.reject("no supporting_experience_ids cited")

        return ValidationResult.accept()
