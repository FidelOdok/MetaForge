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

# Weighted-fusion default split (MET-467): 30% lexical, 70% semantic.
DEFAULT_LEXICAL_WEIGHT = 0.3
DEFAULT_SEMANTIC_WEIGHT = 0.7

# Built-in electronics query synonyms (MET-467 Task 1). A query term that
# matches a key is expanded with the listed phrases so a lexical search for
# "wifi" also hits chunks that only spell out "802.11". Lower-cased keys;
# expansion is additive (the original term is always kept). Callers can
# override / extend via ``HybridRanker(synonyms=...)``.
DEFAULT_SYNONYMS: dict[str, list[str]] = {
    "wifi": ["802.11b", "802.11g", "802.11n", "802.11ac", "wlan"],
    "ble": ["bluetooth low energy", "bluetooth le"],
    "bluetooth": ["ble"],
    "mcu": ["microcontroller"],
    "ldo": ["low dropout regulator"],
    "adc": ["analog to digital converter"],
    "dac": ["digital to analog converter"],
    "uart": ["serial"],
    "imu": ["accelerometer gyroscope"],
}

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    """Lowercase + alphanumeric tokenization (keeps part-number digits)."""
    return _TOKEN_RE.findall(text.lower())


def expand_query(query: str, synonyms: dict[str, list[str]]) -> str:
    """Append synonym phrases for any query token found in ``synonyms``.

    The original query is preserved; matched terms add their synonym
    phrases on the end so BM25 (which works on the token set) also
    matches documents that only use the synonym spelling.
    """
    if not synonyms:
        return query
    extra: list[str] = []
    for token in _tokenize(query):
        for phrase in synonyms.get(token, ()):
            extra.append(phrase)
    return query if not extra else query + " " + " ".join(extra)


def _min_max_normalize(scores: list[float]) -> list[float]:
    """Scale ``scores`` into [0, 1]. All-equal input → all zeros (no signal)."""
    if not scores:
        return []
    lo, hi = min(scores), max(scores)
    span = hi - lo
    if span <= 0.0:
        return [0.0] * len(scores)
    return [(s - lo) / span for s in scores]


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

    def __init__(
        self,
        rrf_k: int = DEFAULT_RRF_K,
        *,
        mode: str = "rrf",
        lexical_weight: float = DEFAULT_LEXICAL_WEIGHT,
        semantic_weight: float = DEFAULT_SEMANTIC_WEIGHT,
        synonyms: dict[str, list[str]] | None = None,
    ) -> None:
        if rrf_k <= 0:
            raise ValueError(f"rrf_k must be > 0, got {rrf_k!r}")
        if mode not in ("rrf", "weighted"):
            raise ValueError(f"mode must be 'rrf' or 'weighted', got {mode!r}")
        if lexical_weight < 0 or semantic_weight < 0:
            raise ValueError("weights must be non-negative")
        if (lexical_weight + semantic_weight) <= 0:
            raise ValueError("weights must not both be zero")
        self._rrf_k = rrf_k
        self._mode = mode
        self._lexical_weight = lexical_weight
        self._semantic_weight = semantic_weight
        # ``None`` opts into the built-in electronics synonyms; pass ``{}`` to
        # disable expansion entirely.
        self._synonyms = DEFAULT_SYNONYMS if synonyms is None else synonyms

    def fuse(self, query: str, hits: list[SearchHit]) -> list[SearchHit]:
        """Return ``hits`` re-ordered by fusing vector similarity with BM25.

        The query is first expanded with any configured synonyms. The two
        signals are then combined either by reciprocal rank fusion
        (``mode="rrf"``) or by a normalized weighted sum
        (``mode="weighted"``, default 30% lexical / 70% semantic). Lists of
        length 0 or 1 are returned unchanged.
        """
        if len(hits) <= 1:
            return list(hits)

        with tracer.start_as_current_span("knowledge.hybrid_fuse") as span:
            span.set_attribute("knowledge.hybrid.candidate_count", len(hits))
            span.set_attribute("knowledge.hybrid.mode", self._mode)
            expanded = expand_query(query, self._synonyms)
            documents = [_tokenize(h.content) for h in hits]
            bm25 = _bm25_scores(expanded, documents)
            vector_scores = [h.similarity_score for h in hits]

            if self._mode == "weighted":
                scores = self._weighted_scores(bm25, vector_scores)
            else:
                scores = self._rrf_scores(bm25, vector_scores)

            order = sorted(range(len(hits)), key=lambda i: (-scores[i], i))
            for new_index, old_index in enumerate(order):
                if new_index != old_index:
                    logger.info(
                        "knowledge_hybrid_promoted",
                        old_index=old_index,
                        new_index=new_index,
                        mode=self._mode,
                        source_path=hits[old_index].source_path,
                    )
            span.set_attribute("knowledge.hybrid.result_count", len(order))
            return [hits[i] for i in order]

    def _rrf_scores(self, bm25: list[float], vector_scores: list[float]) -> list[float]:
        bm25_rank = _rank_index(bm25)
        vector_rank = _rank_index(vector_scores)
        return [
            1.0 / (self._rrf_k + vector_rank[i]) + 1.0 / (self._rrf_k + bm25_rank[i])
            for i in range(len(bm25))
        ]

    def _weighted_scores(self, bm25: list[float], vector_scores: list[float]) -> list[float]:
        bm25_norm = _min_max_normalize(bm25)
        vector_norm = _min_max_normalize(vector_scores)
        return [
            self._lexical_weight * bm25_norm[i] + self._semantic_weight * vector_norm[i]
            for i in range(len(bm25))
        ]
