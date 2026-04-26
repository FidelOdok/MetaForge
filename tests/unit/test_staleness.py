"""Unit tests for context staleness aging (MET-323).

Cover:

* ``compute_staleness`` mapping of age, supersede flag, and shadow
  count to a [0, 1] score with sensible defaults.
* ``annotate_cross_fragment_staleness`` flags older duplicates of the
  same ``source_id`` and leaves the newest one untouched.
* The assembler computes ``staleness_score`` on every fragment and
  drops the ones above ``staleness_threshold`` *before* the budget
  pass — fresh hits aren't stolen by stale ones competing for the
  budget.
* The default threshold (1.0) is back-compat: nothing is filtered.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from digital_twin.context import (
    ContextAssembler,
    ContextAssemblyRequest,
    ContextFragment,
    ContextScope,
    ContextSourceKind,
    compute_staleness,
)
from digital_twin.context.models import estimate_tokens
from digital_twin.context.staleness import (
    STALENESS_HALF_LIFE_SECONDS,
    annotate_cross_fragment_staleness,
)
from digital_twin.knowledge.service import IngestResult, SearchHit
from digital_twin.knowledge.types import KnowledgeType
from twin_core.api import InMemoryTwinAPI

# ---------------------------------------------------------------------------
# compute_staleness
# ---------------------------------------------------------------------------


class TestComputeStaleness:
    def test_no_signals_returns_zero(self) -> None:
        assert compute_staleness({}) == 0.0

    def test_explicit_supersede_forces_one(self) -> None:
        assert compute_staleness({"superseded": True}) == 1.0
        assert compute_staleness({"superseded": "yes"}) == 1.0

    def test_age_decays_to_half_at_half_life(self) -> None:
        now = datetime.now(UTC).timestamp()
        half_life_ago = now - STALENESS_HALF_LIFE_SECONDS
        score = compute_staleness({"created_at": half_life_ago}, now_ts=now)
        assert 0.45 < score < 0.55

    def test_age_clamps_to_under_one_at_old_age(self) -> None:
        now = datetime.now(UTC).timestamp()
        # 5x half-life → ~0.969
        very_old = now - STALENESS_HALF_LIFE_SECONDS * 5
        score = compute_staleness({"created_at": very_old}, now_ts=now)
        assert score > 0.9
        assert score < 1.0

    def test_iso_timestamp_parses(self) -> None:
        now = datetime.now(UTC)
        old_iso = (now - timedelta(days=30)).isoformat()
        score = compute_staleness({"created_at": old_iso}, now_ts=now.timestamp())
        # 30 days = ~one half-life
        assert 0.45 < score < 0.55

    def test_unparseable_timestamp_is_treated_as_unknown_age(self) -> None:
        assert compute_staleness({"created_at": "not-a-date"}) == 0.0

    def test_supersede_dominates_age(self) -> None:
        now = datetime.now(UTC).timestamp()
        # Fresh AND superseded → still 1.0 (max of signals).
        assert compute_staleness({"created_at": now, "superseded": True}, now_ts=now) == 1.0

    def test_shadowed_by_contributes_to_score(self) -> None:
        # shadowed_by=1 contributes 0.5; shadowed_by=2 → 1.0.
        assert compute_staleness({"shadowed_by": 1}) == pytest.approx(0.5, abs=1e-3)
        assert compute_staleness({"shadowed_by": 2}) == 1.0


# ---------------------------------------------------------------------------
# annotate_cross_fragment_staleness
# ---------------------------------------------------------------------------


def _frag(source_id: str, content: str = "x", created_at: float | None = None) -> ContextFragment:
    md: dict[str, Any] = {}
    if created_at is not None:
        md["created_at"] = created_at
    return ContextFragment(
        content=content,
        source_kind=ContextSourceKind.KNOWLEDGE_HIT,
        source_id=source_id,
        token_count=estimate_tokens(content),
        metadata=md,
    )


class TestAnnotateCrossFragment:
    def test_no_duplicates_unchanged(self) -> None:
        fragments = [_frag("a"), _frag("b"), _frag("c")]
        annotate_cross_fragment_staleness(fragments)
        for frag in fragments:
            assert "shadowed_by" not in frag.metadata

    def test_older_duplicate_is_marked(self) -> None:
        now = datetime.now(UTC).timestamp()
        fresh = _frag("doc.md", "new", created_at=now)
        old = _frag("doc.md", "old", created_at=now - 86400)
        annotate_cross_fragment_staleness([fresh, old])
        assert "shadowed_by" not in fresh.metadata
        assert old.metadata["shadowed_by"] == 1

    def test_three_versions_only_oldest_two_shadowed(self) -> None:
        now = datetime.now(UTC).timestamp()
        v3 = _frag("doc.md", "v3", created_at=now)
        v2 = _frag("doc.md", "v2", created_at=now - 86400)
        v1 = _frag("doc.md", "v1", created_at=now - 2 * 86400)
        annotate_cross_fragment_staleness([v1, v2, v3])
        assert "shadowed_by" not in v3.metadata
        assert v2.metadata["shadowed_by"] == 1
        assert v1.metadata["shadowed_by"] == 1


# ---------------------------------------------------------------------------
# Assembler integration
# ---------------------------------------------------------------------------


class _StubService:
    def __init__(self, hits: list[SearchHit]) -> None:
        self._hits = hits

    async def ingest(self, *args: Any, **kwargs: Any) -> IngestResult:  # pragma: no cover
        return IngestResult(entry_ids=[], chunks_indexed=0, source_path="")

    async def search(
        self,
        query: str,
        top_k: int = 5,
        knowledge_type: KnowledgeType | None = None,
        filters: dict[str, Any] | None = None,
    ) -> list[SearchHit]:
        return list(self._hits)

    async def delete_by_source(self, source_path: str) -> int:  # pragma: no cover
        return 0

    async def health_check(self) -> dict[str, Any]:  # pragma: no cover
        return {"status": "ok"}


def _hit(
    content: str, score: float, created_at: str | None = None, superseded: bool = False
) -> SearchHit:
    md: dict[str, Any] = {}
    if created_at is not None:
        md["created_at"] = created_at
    if superseded:
        md["superseded"] = True
    return SearchHit(
        content=content,
        similarity_score=score,
        source_path=f"{content}.md",
        heading="H",
        chunk_index=0,
        total_chunks=1,
        metadata=md,
        knowledge_type=KnowledgeType.DESIGN_DECISION,
        source_work_product_id=None,
    )


@pytest.fixture
def twin() -> InMemoryTwinAPI:
    return InMemoryTwinAPI.create()


class TestAssemblerStalenessFilter:
    async def test_default_threshold_keeps_everything(self, twin: InMemoryTwinAPI) -> None:
        # Default staleness_threshold = 1.0 = no filter, even if some
        # fragments are old or superseded.
        now = datetime.now(UTC)
        old_iso = (now - timedelta(days=365)).isoformat()
        hits = [
            _hit("fresh", 0.9),
            _hit("very-old", 0.8, created_at=old_iso),
            _hit("dropped", 0.7, superseded=True),
        ]
        service = _StubService(hits)
        assembler = ContextAssembler(twin=twin, knowledge_service=service)  # type: ignore[arg-type]
        request = ContextAssemblyRequest(
            agent_id="t",
            query="?",
            scope=[ContextScope.KNOWLEDGE],
        )
        response = await assembler.assemble(request)
        # All three pass through under default threshold.
        contents = {f.content for f in response.fragments}
        assert contents == {"fresh", "very-old", "dropped"}
        # Each fragment carries its score.
        assert all(f.staleness_score is not None for f in response.fragments)

    async def test_strict_threshold_drops_stale(self, twin: InMemoryTwinAPI) -> None:
        now = datetime.now(UTC)
        old_iso = (now - timedelta(days=365)).isoformat()
        hits = [
            _hit("fresh", 0.9),
            _hit("ancient", 0.8, created_at=old_iso),
            _hit("voided", 0.7, superseded=True),
        ]
        service = _StubService(hits)
        assembler = ContextAssembler(twin=twin, knowledge_service=service)  # type: ignore[arg-type]
        request = ContextAssemblyRequest(
            agent_id="t",
            query="?",
            scope=[ContextScope.KNOWLEDGE],
            staleness_threshold=0.4,  # ~ keep < 17 days
        )
        response = await assembler.assemble(request)
        contents = {f.content for f in response.fragments}
        # `ancient` (~0.999) and `voided` (1.0) are dropped.
        assert contents == {"fresh"}
        # And the response metadata calls them out.
        assert response.metadata["stale_dropped_count"] == 2
        assert "ancient.md" in response.metadata["stale_dropped_ids"]
        assert "voided.md" in response.metadata["stale_dropped_ids"]

    async def test_explicit_supersede_always_dropped_under_filter(
        self, twin: InMemoryTwinAPI
    ) -> None:
        # Even at threshold 0.99, supersede=1.0 still gets dropped.
        hits = [_hit("fresh", 0.9), _hit("voided", 0.8, superseded=True)]
        service = _StubService(hits)
        assembler = ContextAssembler(twin=twin, knowledge_service=service)  # type: ignore[arg-type]
        request = ContextAssemblyRequest(
            agent_id="t",
            query="?",
            scope=[ContextScope.KNOWLEDGE],
            staleness_threshold=0.99,
        )
        response = await assembler.assemble(request)
        contents = {f.content for f in response.fragments}
        assert "voided" not in contents
        assert "fresh" in contents

    async def test_score_field_populated_on_every_fragment(self, twin: InMemoryTwinAPI) -> None:
        hits = [_hit("a", 0.9), _hit("b", 0.7)]
        service = _StubService(hits)
        assembler = ContextAssembler(twin=twin, knowledge_service=service)  # type: ignore[arg-type]
        request = ContextAssemblyRequest(
            agent_id="t",
            query="?",
            scope=[ContextScope.KNOWLEDGE],
        )
        response = await assembler.assemble(request)
        for frag in response.fragments:
            assert frag.staleness_score is not None
            assert 0.0 <= frag.staleness_score <= 1.0

    async def test_back_compat_shape_unchanged(self, twin: InMemoryTwinAPI) -> None:
        # Existing fragment construction (no staleness fields) still
        # works; ``staleness_score`` defaults to None.
        frag = ContextFragment(
            content="hello",
            source_kind=ContextSourceKind.KNOWLEDGE_HIT,
            source_id="x",
            token_count=1,
        )
        assert frag.staleness_score is None
        # Response model_validate accepts the new field with default.
        request = ContextAssemblyRequest(
            agent_id="t",
            query="?",
            scope=[ContextScope.KNOWLEDGE],
        )
        # Defaults to 1.0 → no filter — assert via the field.
        assert request.staleness_threshold == 1.0
