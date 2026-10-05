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

**Revision state comes first (FORGE-530).** A fragment whose metadata carries
``item_key``/``item_revision`` (every definition revision is stamped with
them, FORGE-523) is scored by what happened to that revision, not by how old
it is: the current approved revision is fresh however old it is, a superseded
one is 1.0 however new it is, a draft is fresh only for the run that wrote it
(``run_id``) and 1.0 for everyone else, an abandoned one is 1.0, and a rejected
one is 1.0 unless the caller asks for lessons (``include_lessons=True``), when
it is kept as "already tried, failed because". :func:`annotate_revision_state`
fills the state from the twin; age decay still applies to fragments with no
item, or whose state is unknown.

The agent's ``ContextAssemblyRequest.staleness_threshold`` (default 1.0
= no filter) gates the drop. ``staleness_threshold=0.5`` cuts anything
older than ~30 days; ``0.2`` is "freshness-only".
"""

from __future__ import annotations

from typing import Any

import structlog

__all__ = [
    "STALENESS_HALF_LIFE_SECONDS",
    "annotate_cross_fragment_staleness",
    "annotate_revision_state",
    "compute_staleness",
    "revision_staleness",
]

logger = structlog.get_logger(__name__)


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


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def revision_staleness(
    metadata: dict[str, Any],
    *,
    include_lessons: bool = False,
    run_id: str | None = None,
) -> float | None:
    """Score a fragment by its item revision state, or ``None`` when it has none.

    Reads ``item_revision`` plus the state :func:`annotate_revision_state`
    adds (``revision_status``, ``revision_run``, ``item_current_revision``):

    * a draft: 0.0 only for the run that wrote it (``run_id``), 1.0 for
      every other reader, so one run's work in progress never leaks into
      another's retrieval;
    * rejected: 1.0, or 0.0 when ``include_lessons`` (kept as a lesson);
    * abandoned (its run failed or was sent back): 1.0;
    * the current approved revision: 0.0;
    * an older revision than the current one (superseded): 1.0.

    A fragment stamped with a ``change_set`` whose state could not be read is
    treated as a draft of that run: dropping an approved revision for one
    turn is cheaper than showing another run's draft as fact.

    ``None`` (no item, or nothing known about its state) leaves the fragment
    to the age and shadowing signals.
    """
    if not metadata.get("item_key"):
        return None
    revision = _as_int(metadata.get("item_revision"))
    if revision is None:
        return None
    status = str(metadata.get("revision_status") or "").lower()
    writer = metadata.get("revision_run") or metadata.get("change_set")
    if not status and metadata.get("change_set"):
        status = "draft"
    if status == "draft":
        return 0.0 if run_id and writer and str(writer) == str(run_id) else 1.0
    if status == "rejected":
        return 0.0 if include_lessons else 1.0
    if status == "abandoned":
        return 1.0
    current = _as_int(metadata.get("item_current_revision"))
    if current is None:
        return None
    return 0.0 if revision >= current else 1.0


def compute_staleness(
    metadata: dict[str, Any],
    now_ts: float | None = None,
    *,
    include_lessons: bool = False,
    run_id: str | None = None,
) -> float:
    """Return the staleness score for a fragment given its metadata.

    A fragment with item revision state is scored by that state alone
    (:func:`revision_staleness`, FORGE-530); otherwise the score is the
    **max** of the three signals so any one strong signal dominates:

    * Explicit ``superseded`` flag → 1.0.
    * Age curve from ``created_at``.
    * ``shadowed_by`` counter (set by ``annotate_cross_fragment_staleness``)
      contributes ``min(1.0, count × 0.5)``.

    Returns 0.0 when no signal applies — a fragment with neither a
    timestamp nor a superseded flag is treated as fresh.
    """
    if metadata.get("superseded"):
        return 1.0

    by_revision = revision_staleness(metadata, include_lessons=include_lessons, run_id=run_id)
    if by_revision is not None:
        return by_revision

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


async def annotate_revision_state(fragments: list[Any], twin: Any) -> int:
    """Add revision state to every fragment that is a stamped item revision.

    For a fragment whose metadata carries ``item_key`` and whose
    ``work_product_id`` is a revision node, sets ``revision_status``
    (approved/draft/rejected/abandoned, from FORGE-525's
    ``REVISION_OF`` status, see ``twin_core.items.state``), ``revision_run``
    (the run that wrote it), ``item_current_revision`` (the newest
    approved revision), ``item_head_revision`` and, for a rejected
    revision, ``rejection_reason``. Best-effort: a twin without item
    support, or any read failure, leaves the fragment to age decay.
    Returns how many fragments were annotated.
    """
    from twin_core.items import item_for_node, supports_items
    from twin_core.items.state import REJECTED, current_revision, revision_views

    if not fragments or twin is None or not supports_items(twin):
        return 0
    cache: dict[Any, list[Any]] = {}
    annotated = 0
    for frag in fragments:
        md = getattr(frag, "metadata", None)
        node_id = getattr(frag, "work_product_id", None)
        if not isinstance(md, dict) or not md.get("item_key") or node_id is None:
            continue
        try:
            found = await item_for_node(twin, node_id)
            if found is None:
                continue
            item = found[0]
            if item.id not in cache:
                cache[item.id] = await revision_views(twin, item)
            views = cache[item.id]
        except Exception as exc:  # noqa: BLE001 -- staleness must never fail assembly
            logger.debug("revision_state_annotation_failed", node_id=str(node_id), error=str(exc))
            continue
        own = next((v for v in views if v.node_id == node_id), None)
        current = current_revision(views)
        md["item_head_revision"] = item.head_revision
        if current is not None:
            md["item_current_revision"] = current.revision
        if own is not None:
            md["revision_status"] = own.status
            if own.run_id:
                md["revision_run"] = own.run_id
            if own.reason and own.status == REJECTED:
                md["rejection_reason"] = own.reason
        annotated += 1
    return annotated
