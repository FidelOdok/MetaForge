"""Context-fragment staleness detection (MET-323).

Computes a 0.0 (fresh) → 1.0 (fully stale) score for each
``ContextFragment`` so the assembler can drop stale chunks before the
token-budget pass. Three stale signals are combined via ``max``:

* **Age** — exponential ramp on ``metadata["created_at"]``. The
  half-life mirrors MET-317's recency decay (30 days), but the
  staleness curve goes the other direction: 0 at age 0, asymptoting
  to 1 as age grows.
* **Explicit supersede flag** — ``metadata["superseded"] = True`` (or
  any truthy value) forces score 1.0. Callers set this when a newer
  entry overrides an older one — the auto-link comes in MET-307's
  cleanup follow-up.
* **Cross-fragment shadowing** — when two fragments share the same
  ``source_id`` (re-ingested document, knowledge / graph dual mention,
  etc.) the older one's age contribution is bumped by the
  ``newer-than`` margin so it loses to its replacement under any
  threshold strict enough to matter.

The agent's ``ContextAssemblyRequest.staleness_threshold`` (default 1.0
= no filter) gates the drop. ``staleness_threshold=0.5`` cuts anything
older than ~30 days; ``0.2`` is "freshness-only".
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "STALENESS_HALF_LIFE_SECONDS",
    "annotate_cross_fragment_staleness",
    "compute_staleness",
]


STALENESS_HALF_LIFE_SECONDS: float = 30 * 24 * 3600.0
"""30-day half-life for the age component.

A 30-day-old fragment scores 0.5 on age alone; a 90-day-old one
scores ~0.875. Tuned so default decisions don't expire instantly but
year-old data is heavily de-prioritised.
"""


def _parse_timestamp(raw: Any) -> float | None:
    """Coerce ``raw`` to an epoch float, returning ``None`` on failure."""
    if raw is None:
        return None
    if isinstance(raw, int | float):
        return float(raw)
    from datetime import datetime

    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _age_staleness(created_at: float | None, now_ts: float) -> float:
    """Map age in seconds to a [0, 1] staleness score.

    ``1 - exp(-ln(2) × age / half_life)`` — the inverse of the recency
    decay used in MET-317. Score 0 at age 0, 0.5 at half-life, → 1 as
    age → ∞.
    """
    if created_at is None:
        return 0.0  # Unknown age is not penalised; recency does the same.
    age = max(0.0, now_ts - created_at)
    if age <= 0.0:
        return 0.0
    import math

    return 1.0 - math.exp(-math.log(2) * age / STALENESS_HALF_LIFE_SECONDS)


def compute_staleness(metadata: dict[str, Any], now_ts: float | None = None) -> float:
    """Return the staleness score for a fragment given its metadata.

    The score is the **max** of the three signals so any one strong
    signal dominates:

    * Explicit ``superseded`` flag → 1.0.
    * Age curve from ``created_at``.
    * ``shadowed_by`` counter (set by ``annotate_cross_fragment_staleness``)
      contributes ``min(1.0, count × 0.5)``.

    Returns 0.0 when no signal applies — a fragment with neither a
    timestamp nor a superseded flag is treated as fresh.
    """
    if metadata.get("superseded"):
        return 1.0

    if now_ts is None:
        import time

        now_ts = time.time()

    created_at = _parse_timestamp(metadata.get("created_at"))
    age_score = _age_staleness(created_at, now_ts)

    shadowed = metadata.get("shadowed_by")
    shadow_score = 0.0
    if isinstance(shadowed, int) and shadowed > 0:
        shadow_score = min(1.0, shadowed * 0.5)

    return max(age_score, shadow_score)


def annotate_cross_fragment_staleness(
    fragments: list[Any],
    now_ts: float | None = None,
) -> None:
    """Mark older duplicates of the same ``source_id`` as shadowed.

    Two fragments with the same ``source_id`` are likely the same
    document seen at different points in time — pre-MET-307 the
    consumer ``delete_by_source``'d the old one before re-ingest, but
    callers can hand in batches that still contain duplicates (e.g.
    cross-source merging in MET-322).

    This helper mutates each fragment's ``metadata`` dict in place to
    add ``shadowed_by`` count for every fragment older than another
    sharing its ``source_id``. The newest one is left untouched.
    """
    if not fragments:
        return
    if now_ts is None:
        import time

        now_ts = time.time()

    by_source: dict[str, list[tuple[float, Any]]] = {}
    for frag in fragments:
        source_id = getattr(frag, "source_id", None)
        if not source_id:
            continue
        ts = _parse_timestamp(getattr(frag, "metadata", {}).get("created_at"))
        if ts is None:
            ts = 0.0  # Unknown timestamp → oldest in its bucket.
        by_source.setdefault(source_id, []).append((ts, frag))

    for entries in by_source.values():
        if len(entries) < 2:
            continue
        # Newest first.
        entries.sort(key=lambda pair: pair[0], reverse=True)
        for _, older in entries[1:]:
            md = getattr(older, "metadata", None)
            if md is None:
                continue
            md["shadowed_by"] = md.get("shadowed_by", 0) + 1
