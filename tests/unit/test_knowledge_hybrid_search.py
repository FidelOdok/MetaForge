"""Unit tests for hybrid BM25 + vector ranking fusion (MET-465 Task 2)."""

from __future__ import annotations

from digital_twin.knowledge.hybrid_search import (
    HybridRanker,
    _bm25_scores,
    _tokenize,
)
from digital_twin.knowledge.service import SearchHit


def _hit(content: str, similarity: float, source: str = "ds.pdf") -> SearchHit:
    return SearchHit(
        content=content,
        similarity_score=similarity,
        source_path=source,
        heading=None,
        chunk_index=0,
        total_chunks=1,
    )


def test_tokenize_keeps_alphanumeric_partnumbers():
    assert _tokenize("ESP32-WROOM 3.3V supply") == ["esp32", "wroom", "3", "3v", "supply"]


def test_bm25_scores_rank_keyword_match_higher():
    docs = [
        _tokenize("the quick brown fox"),
        _tokenize("low power wifi module esp32"),
        _tokenize("a generic capacitor datasheet"),
    ]
    scores = _bm25_scores("low power wifi", docs)
    # The middle doc is the only one containing the query terms.
    assert scores[1] > scores[0]
    assert scores[1] > scores[2]


def test_bm25_empty_corpus_and_query():
    assert _bm25_scores("anything", []) == []
    assert _bm25_scores("", [_tokenize("some text")]) == [0.0]


def test_fuse_single_or_empty_unchanged():
    ranker = HybridRanker()
    assert ranker.fuse("q", []) == []
    one = [_hit("solo", 0.9)]
    assert ranker.fuse("q", one) == one


def test_fuse_promotes_lexical_hit_that_vector_ranked_last():
    ranker = HybridRanker()
    # h1, h2: strong vector, no lexical overlap. h3: weakest vector, but the
    # only chunk containing the query token. Pure vector order is h1, h2, h3.
    h1 = _hit("thermal paste application notes", 0.90, source="h1.pdf")
    h2 = _hit("generic resistor array overview", 0.80, source="h2.pdf")
    h3 = _hit("integrated wifi module", 0.30, source="h3.pdf")

    fused = [h.source_path for h in ranker.fuse("wifi", [h1, h2, h3])]

    # Fusion pulls the lexically-relevant h3 above the non-lexical h2,
    # which pure vector ranking placed ahead of it.
    assert fused != ["h1.pdf", "h2.pdf", "h3.pdf"]
    assert fused.index("h3.pdf") < fused.index("h2.pdf")


def test_fuse_keeps_dual_strong_hit_on_top():
    ranker = HybridRanker()
    strong = _hit("low power wifi esp32 module", 0.95, source="strong.pdf")
    weak = _hit("low power wifi", 0.40, source="weak.pdf")
    noise = _hit("totally unrelated content here", 0.50, source="noise.pdf")

    fused = ranker.fuse("low power wifi esp32", [strong, weak, noise])

    assert fused[0].source_path == "strong.pdf"
    assert fused[-1].source_path == "noise.pdf"


def test_fuse_is_deterministic():
    ranker = HybridRanker()
    hits = [
        _hit("alpha beta gamma", 0.7, source="1.pdf"),
        _hit("beta gamma delta", 0.6, source="2.pdf"),
        _hit("gamma delta epsilon", 0.5, source="3.pdf"),
    ]
    first = [h.source_path for h in ranker.fuse("beta gamma", list(hits))]
    second = [h.source_path for h in ranker.fuse("beta gamma", list(hits))]
    assert first == second


def test_invalid_rrf_k_rejected():
    import pytest

    with pytest.raises(ValueError, match="rrf_k must be > 0"):
        HybridRanker(rrf_k=0)
