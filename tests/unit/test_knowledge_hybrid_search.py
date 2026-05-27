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


# ---------------------------------------------------------------------------
# Synonym expansion (MET-467 Task 1)
# ---------------------------------------------------------------------------


def test_expand_query_appends_synonyms():
    from digital_twin.knowledge.hybrid_search import DEFAULT_SYNONYMS, expand_query

    expanded = expand_query("low power wifi", DEFAULT_SYNONYMS)
    assert expanded.startswith("low power wifi")
    assert "802.11n" in expanded


def test_expand_query_no_synonyms_is_identity():
    from digital_twin.knowledge.hybrid_search import expand_query

    assert expand_query("low power wifi", {}) == "low power wifi"


def test_synonym_expansion_lets_query_match_synonym_spelling():
    # Lexical-only weighting so the result is driven purely by BM25.
    # d1 only spells out the synonym ("802.11n"), never the literal "wifi",
    # and is listed second in input order.
    d2 = _hit("generic passive component", 0.90, source="other.pdf")
    d1 = _hit("module supports 802.11n connectivity", 0.30, source="syn.pdf")

    with_syn = HybridRanker(mode="weighted", lexical_weight=1.0, semantic_weight=0.0)
    without_syn = HybridRanker(
        mode="weighted", lexical_weight=1.0, semantic_weight=0.0, synonyms={}
    )

    # "wifi" expands to include "802.11n", so d1 gains a lexical match and is
    # promoted to the top despite being second in input order.
    assert with_syn.fuse("wifi", [d2, d1])[0].source_path == "syn.pdf"
    # Without expansion neither doc contains "wifi" → no lexical signal, so the
    # tie keeps input order and d2 stays first.
    assert without_syn.fuse("wifi", [d2, d1])[0].source_path == "other.pdf"


# ---------------------------------------------------------------------------
# Weighted fusion (MET-467 Task 3)
# ---------------------------------------------------------------------------


def test_min_max_normalize():
    from digital_twin.knowledge.hybrid_search import _min_max_normalize

    assert _min_max_normalize([]) == []
    assert _min_max_normalize([5.0, 5.0, 5.0]) == [0.0, 0.0, 0.0]
    out = _min_max_normalize([0.0, 5.0, 10.0])
    assert out[0] == 0.0 and out[-1] == 1.0 and 0.0 < out[1] < 1.0


def test_weighted_semantic_only_orders_by_vector():
    ranker = HybridRanker(mode="weighted", lexical_weight=0.0, semantic_weight=1.0)
    # A keyword-rich but low-vector hit must NOT win when weight is 100% semantic.
    kw = _hit("wifi wifi wifi module", 0.20, source="kw.pdf")
    hi = _hit("unrelated content", 0.95, source="hi.pdf")
    mid = _hit("other content", 0.50, source="mid.pdf")

    fused = [h.source_path for h in ranker.fuse("wifi", [kw, hi, mid])]
    assert fused == ["hi.pdf", "mid.pdf", "kw.pdf"]


def test_weighted_lexical_only_orders_by_bm25():
    ranker = HybridRanker(mode="weighted", lexical_weight=1.0, semantic_weight=0.0)
    # The keyword hit wins despite the lowest vector score.
    kw = _hit("esp32 wifi module", 0.10, source="kw.pdf")
    a = _hit("resistor array", 0.90, source="a.pdf")
    b = _hit("capacitor bank", 0.80, source="b.pdf")

    assert ranker.fuse("esp32 wifi", [kw, a, b])[0].source_path == "kw.pdf"


def test_weighted_default_split_is_semantic_dominant():
    # Default 30/70 keeps a strongly-semantic hit ahead of a purely-lexical one.
    ranker = HybridRanker(mode="weighted")
    semantic = _hit("low dropout regulator topology", 0.95, source="sem.pdf")
    lexical = _hit("wifi", 0.10, source="lex.pdf")

    fused = [h.source_path for h in ranker.fuse("wifi", [semantic, lexical])]
    assert fused[0] == "sem.pdf"


def test_invalid_mode_rejected():
    import pytest

    with pytest.raises(ValueError, match="mode must be"):
        HybridRanker(mode="fuzzy")


def test_invalid_weights_rejected():
    import pytest

    with pytest.raises(ValueError, match="must not both be zero"):
        HybridRanker(mode="weighted", lexical_weight=0.0, semantic_weight=0.0)
    with pytest.raises(ValueError, match="non-negative"):
        HybridRanker(mode="weighted", lexical_weight=-1.0)
