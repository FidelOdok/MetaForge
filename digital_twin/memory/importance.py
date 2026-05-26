"""Importance scoring for agent-task experiences.

Score = ``w_recency * recency + w_relevance * relevance + w_criticality * criticality``

The default weight split (recency 0.20, relevance 0.40, criticality 0.40) is
the contract pinned in MET-453. Tweak via ``ImportanceWeights`` if needed
for experimentation, but production callers should rely on the default.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime

from orchestrator.event_bus.events import Event, EventType

DEFAULT_RECENCY_HALF_LIFE_HOURS = 24.0
"""Hours after which the recency component decays to 0.5."""


@dataclass(frozen=True)
class ImportanceWeights:
    """Linear weights applied to recency, relevance, and criticality.

    Weights must sum to 1.0 for the resulting score to land in ``[0, 1]``.
    """

    recency: float = 0.20
    relevance: float = 0.40
    criticality: float = 0.40

    def __post_init__(self) -> None:
        total = self.recency + self.relevance + self.criticality
        if not math.isclose(total, 1.0, abs_tol=1e-6):
            raise ValueError(
                f"ImportanceWeights must sum to 1.0, got {total!r}"
                f" (recency={self.recency}, relevance={self.relevance},"
                f" criticality={self.criticality})"
            )


DEFAULT_WEIGHTS = ImportanceWeights()


@dataclass(frozen=True)
class ImportanceScore:
    """Decomposed importance score for an event."""

    recency: float
    relevance: float
    criticality: float
    total: float


def score_importance(
    event: Event,
    *,
    now: datetime | None = None,
    weights: ImportanceWeights = DEFAULT_WEIGHTS,
    recency_half_life_hours: float = DEFAULT_RECENCY_HALF_LIFE_HOURS,
) -> ImportanceScore:
    """Score an ``AGENT_TASK_*`` event for memory indexing.

    Each sub-score is normalized to ``[0, 1]`` and weighted per
    ``weights``; the total is also clamped to ``[0, 1]`` to defend
    against floating-point drift on the boundary.
    """
    reference_now = now or datetime.now(UTC)
    recency = _recency_score(event, reference_now, recency_half_life_hours)
    relevance = _relevance_score(event)
    criticality = _criticality_score(event)
    total = (
        weights.recency * recency
        + weights.relevance * relevance
        + weights.criticality * criticality
    )
    return ImportanceScore(
        recency=recency,
        relevance=relevance,
        criticality=criticality,
        total=max(0.0, min(1.0, total)),
    )


def _recency_score(event: Event, now: datetime, half_life_hours: float) -> float:
    """Exponential decay with the given half-life, clamped to ``[0, 1]``.

    Missing or unparseable timestamps fall back to 0.5 — we should not
    discard the event, but we also should not pretend it is fresh.
    """
    try:
        ts = datetime.fromisoformat(event.timestamp)
    except (TypeError, ValueError):
        return 0.5
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    age_seconds = max(0.0, (now - ts).total_seconds())
    age_hours = age_seconds / 3600.0
    if half_life_hours <= 0:
        return 1.0 if age_hours == 0 else 0.0
    return float(0.5 ** (age_hours / half_life_hours))


def _relevance_score(event: Event) -> float:
    """Crude proxy: relevance scales with how much payload signal is present.

    Real relevance is goal-dependent and only knowable at query time. For
    indexing-side scoring we approximate using the richness of the
    structured payload — an event with `result`, `error`, and a long
    `step_id` is more informative than a bare STARTED event.
    """
    data = event.data or {}
    signal_keys = ("result", "error", "result_summary", "duration", "task_type")
    present = sum(1 for k in signal_keys if data.get(k))
    base = present / float(len(signal_keys))

    # Long step / agent identifiers hint at richer downstream context.
    text_keys = ("step_id", "agent_code", "run_id")
    text_len = sum(len(str(data.get(k, ""))) for k in text_keys)
    text_bonus = min(0.2, text_len / 200.0)  # cap contribution at 0.2
    return max(0.0, min(1.0, base + text_bonus))


def _criticality_score(event: Event) -> float:
    """Failures are most critical; completed-with-result events next; STARTED last.

    Returns ``1.0`` for ``AGENT_TASK_FAILED``, ``0.6`` for
    ``AGENT_TASK_COMPLETED`` with a non-empty result, ``0.4`` for a
    bare completion, and ``0.2`` for ``AGENT_TASK_STARTED``. Anything
    else falls back to ``0.3`` — uncategorized but kept above zero so
    novel event types still index.
    """
    if event.type == EventType.AGENT_TASK_FAILED:
        return 1.0
    if event.type == EventType.AGENT_TASK_COMPLETED:
        return 0.6 if event.data.get("result") else 0.4
    if event.type == EventType.AGENT_TASK_STARTED:
        return 0.2
    return 0.3
