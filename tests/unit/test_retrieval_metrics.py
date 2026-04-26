"""Unit tests for ``digital_twin.context.retrieval_metrics`` (MET-326)."""

from __future__ import annotations

import math
from collections.abc import Sequence

import pytest

from digital_twin.context.retrieval_metrics import (
    f1_at_k,
    mean_reciprocal_rank,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
)

# ---------------------------------------------------------------------------
# precision_at_k
# ---------------------------------------------------------------------------


class TestPrecisionAtK:
    def test_perfect_top_k_is_one(self) -> None:
        assert precision_at_k(["a", "b", "c"], {"a", "b", "c"}, k=3) == 1.0

    def test_no_relevant_in_retrieved_is_zero(self) -> None:
        assert precision_at_k(["x", "y", "z"], {"a", "b"}, k=3) == 0.0

    def test_partial_hit_fraction(self) -> None:
        # 1 of the top 3 is relevant
        assert precision_at_k(["a", "x", "y"], {"a", "b"}, k=3) == pytest.approx(1 / 3)

    def test_k_larger_than_retrieved_clamps(self) -> None:
        # only 2 retrieved; both relevant → 1.0 at k=10
        assert precision_at_k(["a", "b"], {"a", "b"}, k=10) == 1.0

    def test_empty_retrieved_returns_zero(self) -> None:
        assert precision_at_k([], {"a"}, k=5) == 0.0

    def test_invalid_k_raises(self) -> None:
        with pytest.raises(ValueError):
            precision_at_k(["a"], {"a"}, k=0)


# ---------------------------------------------------------------------------
# recall_at_k
# ---------------------------------------------------------------------------


class TestRecallAtK:
    def test_all_relevant_in_top_k_is_one(self) -> None:
        assert recall_at_k(["a", "b"], {"a", "b"}, k=5) == 1.0

    def test_partial_recall(self) -> None:
        # 2 of 4 relevant items retrieved
        assert recall_at_k(["a", "b", "x"], {"a", "b", "c", "d"}, k=5) == 0.5

    def test_top_k_truncation_lowers_recall(self) -> None:
        # only the top 1 considered → 1/3 hit
        assert recall_at_k(["a", "b", "c"], {"a", "b", "c"}, k=1) == pytest.approx(1 / 3)

    def test_empty_relevant_returns_zero(self) -> None:
        assert recall_at_k(["a", "b"], set(), k=5) == 0.0

    def test_invalid_k_raises(self) -> None:
        with pytest.raises(ValueError):
            recall_at_k(["a"], {"a"}, k=0)


# ---------------------------------------------------------------------------
# f1_at_k
# ---------------------------------------------------------------------------


class TestF1AtK:
    def test_perfect_retrieval_is_one(self) -> None:
        assert f1_at_k(["a", "b"], {"a", "b"}, k=2) == 1.0

    def test_zero_when_no_overlap(self) -> None:
        assert f1_at_k(["x"], {"a"}, k=1) == 0.0

    def test_harmonic_mean_property(self) -> None:
        # P=1/3, R=1.0 → F1 = 2*1/3*1 / (1/3 + 1) = (2/3) / (4/3) = 0.5
        retrieved = ["a", "x", "y"]
        relevant = {"a"}
        score = f1_at_k(retrieved, relevant, k=3)
        assert score == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# mean_reciprocal_rank
# ---------------------------------------------------------------------------


class TestMeanReciprocalRank:
    def test_first_hit_at_position_one_gives_one(self) -> None:
        assert mean_reciprocal_rank([["a", "b"]], [{"a"}]) == 1.0

    def test_first_hit_at_position_three_gives_one_third(self) -> None:
        assert mean_reciprocal_rank([["x", "y", "a"]], [{"a"}]) == pytest.approx(1 / 3)

    def test_no_relevant_hit_contributes_zero(self) -> None:
        # 2 queries: first finds at rank 1, second finds nothing → mean 0.5
        rankings: list[Sequence[str]] = [["a"], ["x", "y"]]
        relevants: list[set[str]] = [{"a"}, {"missing"}]
        assert mean_reciprocal_rank(rankings, relevants) == pytest.approx(0.5)

    def test_empty_rankings_returns_zero(self) -> None:
        assert mean_reciprocal_rank([], []) == 0.0

    def test_mismatched_lengths_raise(self) -> None:
        with pytest.raises(ValueError):
            mean_reciprocal_rank([["a"]], [{"a"}, {"b"}])


# ---------------------------------------------------------------------------
# ndcg_at_k
# ---------------------------------------------------------------------------


class TestNdcgAtK:
    def test_perfect_ranking_is_one(self) -> None:
        # Two graded items, retrieved in ideal order.
        retrieved = ["a", "b"]
        grades = {"a": 1.0, "b": 0.5}
        assert ndcg_at_k(retrieved, grades, k=2) == pytest.approx(1.0)

    def test_irrelevant_items_dont_help(self) -> None:
        # No relevant content in top-k → 0
        assert ndcg_at_k(["x", "y", "z"], {"a": 1.0}, k=3) == 0.0

    def test_swapped_positions_lower_score(self) -> None:
        # Higher-graded item demoted to position 2 → less than 1.0
        retrieved = ["b", "a"]  # grade 0.5 then grade 1.0
        grades = {"a": 1.0, "b": 0.5}
        score = ndcg_at_k(retrieved, grades, k=2)
        # DCG_actual = 0.5/log2(2) + 1.0/log2(3) = 0.5 + 0.6309 ≈ 1.1309
        # DCG_ideal  = 1.0/log2(2) + 0.5/log2(3) = 1.0 + 0.3155 ≈ 1.3155
        assert 0.0 < score < 1.0
        expected_actual = 0.5 / math.log2(2) + 1.0 / math.log2(3)
        expected_ideal = 1.0 / math.log2(2) + 0.5 / math.log2(3)
        assert score == pytest.approx(expected_actual / expected_ideal)

    def test_empty_retrieved_returns_zero(self) -> None:
        assert ndcg_at_k([], {"a": 1.0}, k=5) == 0.0

    def test_empty_relevances_returns_zero(self) -> None:
        assert ndcg_at_k(["a", "b"], {}, k=5) == 0.0

    def test_invalid_k_raises(self) -> None:
        with pytest.raises(ValueError):
            ndcg_at_k(["a"], {"a": 1.0}, k=0)
