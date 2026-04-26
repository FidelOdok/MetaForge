"""Retrieval quality metrics — precision@k, recall@k, MRR, NDCG (MET-326).

Pure-math helpers that score a list of retrieved source identifiers
against a labeled set of relevant ones. The L1 evaluator
(``retrieval_evaluator.py``) drives them; agents can also call them
ad-hoc.

All functions take **string identifiers** (typically `source_path` or
``work_product://<uuid>``), not full ``ContextFragment`` rows. That
keeps the math agnostic of the rest of the assembler — feed it
anything you have a relevance label for.

The metric values land in:

* ``observability.metrics.MetricsRegistry``:
  - ``metaforge_retrieval_precision_at_k`` (histogram)
  - ``metaforge_retrieval_recall_at_k`` (histogram)
  - ``metaforge_retrieval_mrr`` (histogram)
  - ``metaforge_retrieval_ndcg_at_k`` (histogram)
  - ``metaforge_context_truncated_total`` (counter — wires the
    MET-317 ``context_truncated`` event)
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

__all__ = [
    "f1_at_k",
    "mean_reciprocal_rank",
    "ndcg_at_k",
    "precision_at_k",
    "recall_at_k",
]


def precision_at_k(
    retrieved: Sequence[str],
    relevant: Iterable[str],
    k: int,
) -> float:
    """Fraction of the top-``k`` retrieved that are relevant.

    Parameters
    ----------
    retrieved
        Retrieved identifiers in rank order (most relevant first).
    relevant
        Set of identifiers known to be relevant for this query.
    k
        Cutoff. Must be ≥ 1.

    Returns
    -------
    float in [0, 1]. ``0.0`` when ``retrieved`` is empty.

    Edge cases:
    * ``k`` larger than ``len(retrieved)`` clamps to the available rows.
    * Duplicates in ``retrieved`` are counted once each — calling code
      should pre-dedupe if it doesn't want that.
    """
    if k < 1:
        raise ValueError(f"k must be >= 1, got {k}")
    relevant_set = set(relevant)
    if not retrieved:
        return 0.0
    top_k = list(retrieved)[:k]
    if not top_k:
        return 0.0
    hits = sum(1 for sid in top_k if sid in relevant_set)
    return hits / len(top_k)


def recall_at_k(
    retrieved: Sequence[str],
    relevant: Iterable[str],
    k: int,
) -> float:
    """Fraction of relevant items captured in the top-``k`` retrieved.

    Returns ``0.0`` when ``relevant`` is empty (no signal — caller's
    eval set is malformed).
    """
    if k < 1:
        raise ValueError(f"k must be >= 1, got {k}")
    relevant_set = set(relevant)
    if not relevant_set:
        return 0.0
    top_k = list(retrieved)[:k]
    hits = sum(1 for sid in top_k if sid in relevant_set)
    return hits / len(relevant_set)


def f1_at_k(
    retrieved: Sequence[str],
    relevant: Iterable[str],
    k: int,
) -> float:
    """Harmonic mean of precision@k and recall@k.

    Returns ``0.0`` when either component is zero (no spurious lift
    when one signal is missing).
    """
    relevant_set = set(relevant)
    p = precision_at_k(retrieved, relevant_set, k)
    r = recall_at_k(retrieved, relevant_set, k)
    if p == 0.0 or r == 0.0:
        return 0.0
    return 2 * p * r / (p + r)


def mean_reciprocal_rank(
    rankings: Sequence[Sequence[str]],
    relevants: Sequence[Iterable[str]],
) -> float:
    """Mean of ``1 / rank-of-first-relevant`` across queries.

    Parameters
    ----------
    rankings
        One retrieved-list per query (rank order).
    relevants
        Parallel sequence: relevant set for each query.

    Returns ``0.0`` when ``rankings`` is empty. A query with no
    relevant hit in its retrieved list contributes ``0.0`` to the
    mean (standard MRR definition).
    """
    if len(rankings) != len(relevants):
        raise ValueError(
            f"rankings ({len(rankings)}) and relevants ({len(relevants)}) "
            "must be parallel sequences"
        )
    if not rankings:
        return 0.0
    total = 0.0
    for retrieved, relevant in zip(rankings, relevants, strict=True):
        relevant_set = set(relevant)
        rr = 0.0
        for rank, sid in enumerate(retrieved, start=1):
            if sid in relevant_set:
                rr = 1.0 / rank
                break
        total += rr
    return total / len(rankings)


def ndcg_at_k(
    retrieved: Sequence[str],
    relevances: dict[str, float],
    k: int,
) -> float:
    """Normalised Discounted Cumulative Gain at cutoff ``k``.

    Parameters
    ----------
    retrieved
        Retrieved identifiers in rank order.
    relevances
        Mapping of identifier → graded relevance (0 = irrelevant,
        higher = more relevant). Identifiers absent from this map are
        treated as 0.
    k
        Cutoff. Must be ≥ 1.

    Returns ``0.0`` when there are no positive relevances or
    ``retrieved`` is empty. Returns ``1.0`` when retrieval is perfect.
    """
    if k < 1:
        raise ValueError(f"k must be >= 1, got {k}")
    if not retrieved or not relevances:
        return 0.0

    def _dcg(scores: Sequence[float]) -> float:
        return sum(score / math.log2(rank + 2) for rank, score in enumerate(scores))

    actual_scores = [float(relevances.get(sid, 0.0)) for sid in list(retrieved)[:k]]
    actual = _dcg(actual_scores)

    ideal_scores = sorted(relevances.values(), reverse=True)[:k]
    ideal = _dcg(ideal_scores)

    if ideal == 0.0:
        return 0.0
    return actual / ideal
