"""Unit tests for the supersedes-aware search filter (MET-447)."""

from __future__ import annotations

from digital_twin.knowledge.lightrag_service import _hit_is_visible
from digital_twin.knowledge.service import SearchHit


def _hit(*, superseded: bool | None = None) -> SearchHit:
    metadata: dict = {}
    if superseded is not None:
        metadata["superseded"] = superseded
    return SearchHit(
        content="x",
        similarity_score=1.0,
        source_path=None,
        heading=None,
        chunk_index=None,
        total_chunks=None,
        metadata=metadata,
    )


class TestHitIsVisible:
    def test_unmarked_chunk_visible_by_default(self) -> None:
        assert _hit_is_visible(_hit(), include_historical=False) is True

    def test_explicit_false_marker_visible(self) -> None:
        """``superseded=False`` is the active-revision marker — keep it."""
        assert _hit_is_visible(_hit(superseded=False), include_historical=False) is True

    def test_superseded_chunk_hidden_by_default(self) -> None:
        assert _hit_is_visible(_hit(superseded=True), include_historical=False) is False

    def test_include_historical_bypasses_filter(self) -> None:
        """Audit / citation queries pass include_historical=True to see all."""
        assert _hit_is_visible(_hit(superseded=True), include_historical=True) is True

    def test_include_historical_does_not_affect_active(self) -> None:
        assert _hit_is_visible(_hit(superseded=False), include_historical=True) is True
        assert _hit_is_visible(_hit(), include_historical=True) is True

    def test_truthy_marker_other_than_bool_true(self) -> None:
        """Any truthy value at metadata.superseded marks the chunk as hidden."""
        hit = _hit()
        hit.metadata["superseded"] = "yes"
        assert _hit_is_visible(hit, include_historical=False) is False
