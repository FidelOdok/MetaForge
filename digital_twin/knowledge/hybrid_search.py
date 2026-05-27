"""Hybrid BM25 + vector ranking fusion for the L1 knowledge layer (MET-465 Task 2).

Vector search alone ranks chunks by embedding cosine similarity, which is
strong on paraphrase / semantic match but weak on exact lexical hits
(part numbers, spec tokens like ``3.3V`` or ``ESP32-WROOM``). This module
adds the *lexical* half: a dependency-free Okapi BM25 scorer over the
candidate ``SearchHit`` set, fused with the existing vector score via
**reciprocal rank fusion** (RRF).

RRF is used rather than a weighted score sum because BM25 and cosine live
on incomparable scales; fusing by *rank* sidesteps normalization and is
the standard, robust choice for lexical+semantic hybrids:

    fused(hit) = 1/(k + rank_vector) + 1/(k + rank_bm25)

Like the cross-encoder ``Reranker``, this is a pure function of
``(query, hits)`` — it only re-orders the list, never mutates a hit or
touches the store, and pulls in no heavy dependencies (no torch, no
external BM25 lib), so it is safe to run on every search.
"""

from __future__ import annotations

import math
import re
from collections import Counter

import structlog

from digital_twin.knowledge.service import SearchHit
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("digital_twin.knowledge.hybrid_search")

DEFAULT_RRF_K = 60
"""Standard reciprocal-rank-fusion constant. Larger values flatten the
contribution of top ranks; 60 is the value from the original RRF paper."""

BM25_K1 = 1.5
BM25_B = 0.75

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    """Lowercase + alphanumeric tokenization (keeps part-number digits)."""
    return _TOKEN_RE.findall(text.lower())


def _bm25_scores(query: str, documents: list[list[str]]) -> list[float]:
    """Okapi BM25 score of ``query`` against each tokenized document.

    The candidate hit set *is* the corpus — IDF is computed over the
    documents being ranked, which is what we want when re-ranking a
    retrieved shortlist. Returns one score per document, in order.
    """
    n = len(documents)
    if n == 0:
        return []
    query_terms = set(_tokenize(query))
    if not query_terms:
        return [0.0] * n

    doc_lengths = [len(doc) for doc in documents]
    avgdl = (sum(doc_lengths) / n) or 1.0
    # Document frequency per query term.
    df: Counter[str] = Counter()
    for doc in documents:
        seen = set(doc)
        for term in query_terms:
            if term in seen:
                df[term] += 1

    scores: list[float] = []
    for doc, dl in zip(documents, doc_lengths, strict=True):
        tf = Counter(doc)
        score = 0.0
        for term in query_terms:
            term_freq = tf.get(term, 0)
            if term_freq == 0:
                continue
            # BM25 idf with the +1 guard so it never goes negative.
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            denom = term_freq + BM25_K1 * (1 - BM25_B + BM25_B * dl / avgdl)
            score += idf * (term_freq * (BM25_K1 + 1)) / denom
        scores.append(score)
    return scores


def _rank_index(scores: list[float]) -> dict[int, int]:
    """Map each document index → its 0-based rank (highest score = rank 0).

    Ties keep input order (stable), so fusion is deterministic.
    """
    order = sorted(range(len(scores)), key=lambda i: (-scores[i], i))
    return {doc_index: rank for rank, doc_index in enumerate(order)}


class HybridRanker:
    """Re-order ``SearchHit`` lists by fusing vector similarity with BM25."""

    def __init__(self, rrf_k: int = DEFAULT_RRF_K) -> None:
        if rrf_k <= 0:
            raise ValueError(f"rrf_k must be > 0, got {rrf_k!r}")
        self._rrf_k = rrf_k

    def fuse(self, query: str, hits: list[SearchHit]) -> list[SearchHit]:
        """Return ``hits`` re-ordered by reciprocal-rank fusion of the
        vector similarity and BM25 lexical scores.

        Lists of length 0 or 1 are returned unchanged (no work to do).
        """
        if len(hits) <= 1:
            return list(hits)

        with tracer.start_as_current_span("knowledge.hybrid_fuse") as span:
            span.set_attribute("knowledge.hybrid.candidate_count", len(hits))
            documents = [_tokenize(h.content) for h in hits]
            bm25 = _bm25_scores(query, documents)
            vector_scores = [h.similarity_score for h in hits]

            bm25_rank = _rank_index(bm25)
            vector_rank = _rank_index(vector_scores)

            def fused(i: int) -> float:
                return 1.0 / (self._rrf_k + vector_rank[i]) + 1.0 / (self._rrf_k + bm25_rank[i])

            order = sorted(range(len(hits)), key=lambda i: (-fused(i), i))
            for new_index, old_index in enumerate(order):
                if new_index != old_index:
                    logger.info(
                        "knowledge_hybrid_promoted",
                        old_index=old_index,
                        new_index=new_index,
                        bm25_rank=bm25_rank[old_index],
                        vector_rank=vector_rank[old_index],
                        source_path=hits[old_index].source_path,
                    )
            span.set_attribute("knowledge.hybrid.result_count", len(order))
            return [hits[i] for i in order]
