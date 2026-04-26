"""Unit tests for token budget management (MET-317).

Cover:

* ``estimate_tokens`` switches from a char heuristic to real BPE
  counts via tiktoken; falls back gracefully when tiktoken is missing.
* ``fragment_priority`` composes recency × authority × relevance, with
  sane defaults for missing inputs.
* ``ContextAssembler._rank`` orders fragments by composite priority,
  with deterministic tie-breaks (newer ``created_at`` wins; smaller
  fragment wins).
* The assembler enforces the budget exactly: at-budget passes, over-
  budget drops the lowest priority fragment, and the
  ``context_truncated`` log fires with a per-source-kind tally.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import pytest

from digital_twin.context import (
    ContextAssembler,
    ContextAssemblyRequest,
    ContextFragment,
    ContextScope,
    ContextSourceKind,
)
from digital_twin.context.models import (
    _ENCODER_CACHE,
    estimate_tokens,
    fragment_priority,
)
from digital_twin.knowledge.service import IngestResult, SearchHit
from digital_twin.knowledge.types import KnowledgeType
from twin_core.api import InMemoryTwinAPI

# ---------------------------------------------------------------------------
# estimate_tokens
# ---------------------------------------------------------------------------


class TestEstimateTokens:
    def test_empty_string_is_zero(self) -> None:
        assert estimate_tokens("") == 0

    def test_short_string_yields_at_least_one(self) -> None:
        assert estimate_tokens("a") >= 1

    def test_tiktoken_count_matches_bpe(self) -> None:
        # "hello world" is exactly 2 BPE tokens under cl100k / gpt-4o-mini.
        assert estimate_tokens("hello world") == 2

    def test_falls_back_to_char_heuristic_when_tiktoken_missing(self) -> None:
        # Force the module-level cache to "tiktoken unavailable".
        original = dict(_ENCODER_CACHE)
        _ENCODER_CACHE.clear()
        with patch.dict("sys.modules", {"tiktoken": None}):  # ImportError on `import tiktoken`
            try:
                # 32-char string → 32 // 4 = 8 fallback tokens.
                assert estimate_tokens("a" * 32) == 8
            finally:
                _ENCODER_CACHE.clear()
                _ENCODER_CACHE.update(original)


# ---------------------------------------------------------------------------
# fragment_priority
# ---------------------------------------------------------------------------


class TestFragmentPriority:
    def test_higher_authority_for_user_input(self) -> None:
        kh = fragment_priority("knowledge_hit", similarity=0.9, metadata={})
        ui = fragment_priority("user_input", similarity=0.9, metadata={})
        assert ui > kh

    def test_recency_decays_with_age(self) -> None:
        now = datetime.now(UTC)
        fresh = fragment_priority(
            "knowledge_hit",
            similarity=0.8,
            metadata={"created_at": now.isoformat()},
        )
        # 60 days = two half-lives → ~0.25× recency multiplier.
        old_iso = (now - timedelta(days=60)).isoformat()
        old = fragment_priority(
            "knowledge_hit",
            similarity=0.8,
            metadata={"created_at": old_iso},
        )
        assert fresh > old
        assert old / fresh < 0.5

    def test_missing_similarity_defaults_to_half(self) -> None:
        # 0.5 fallback so graph fragments still rank.
        prio = fragment_priority("graph_node", similarity=None, metadata={})
        # Recency = 1, authority = 0.85 (graph_node), relevance = 0.5
        # → 0.425
        assert prio == pytest.approx(0.425, abs=1e-3)

    def test_unknown_source_kind_uses_floor_authority(self) -> None:
        prio = fragment_priority("alien_kind", similarity=1.0, metadata={})
        # Default authority 0.5; recency 1; relevance 1 → 0.5
        assert prio == pytest.approx(0.5, abs=1e-3)


# ---------------------------------------------------------------------------
# Assembler ranking + budget enforcement
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
        if knowledge_type is not None:
            return [h for h in self._hits if h.knowledge_type == knowledge_type]
        return list(self._hits)

    async def delete_by_source(self, source_path: str) -> int:  # pragma: no cover
        return 0

    async def health_check(self) -> dict[str, Any]:  # pragma: no cover
        return {"status": "ok"}


def _hit(content: str, score: float, created_at: str | None = None) -> SearchHit:
    md: dict[str, Any] = {}
    if created_at is not None:
        md["created_at"] = created_at
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


class TestAssemblerRanking:
    async def test_ranking_respects_composite_priority(self, twin: InMemoryTwinAPI) -> None:
        now = datetime.now(UTC)
        old_iso = (now - timedelta(days=120)).isoformat()
        new_iso = now.isoformat()
        # Two hits with the same similarity but different recency —
        # newer should sort first under the recency-aware composite.
        hits = [
            _hit("old", 0.8, created_at=old_iso),
            _hit("new", 0.8, created_at=new_iso),
        ]
        service = _StubService(hits)
        assembler = ContextAssembler(twin=twin, knowledge_service=service)  # type: ignore[arg-type]
        request = ContextAssemblyRequest(
            agent_id="test_agent",
            query="?",
            scope=[ContextScope.KNOWLEDGE],
        )
        response = await assembler.assemble(request)
        # New first because recency decays old.
        assert [f.content for f in response.fragments] == ["new", "old"]


class TestBudgetEnforcement:
    async def test_under_budget_keeps_everything(self, twin: InMemoryTwinAPI) -> None:
        hits = [_hit("a", 0.9), _hit("b", 0.7)]
        service = _StubService(hits)
        assembler = ContextAssembler(twin=twin, knowledge_service=service)  # type: ignore[arg-type]
        request = ContextAssemblyRequest(
            agent_id="test",
            query="?",
            scope=[ContextScope.KNOWLEDGE],
            token_budget=10_000,
        )
        response = await assembler.assemble(request)
        assert len(response.fragments) == 2
        assert response.truncated is False
        assert response.dropped_source_ids == []

    async def test_exactly_at_budget_keeps_everything(self, twin: InMemoryTwinAPI) -> None:
        hits = [_hit("a", 0.9), _hit("b", 0.7)]
        service = _StubService(hits)
        assembler = ContextAssembler(twin=twin, knowledge_service=service)  # type: ignore[arg-type]
        # Pre-compute total cost so the budget == exactly the sum.
        assembled_dry = await assembler.assemble(
            ContextAssemblyRequest(
                agent_id="t",
                query="?",
                scope=[ContextScope.KNOWLEDGE],
                token_budget=10_000,
            )
        )
        exact_budget = sum(f.token_count for f in assembled_dry.fragments)
        response = await assembler.assemble(
            ContextAssemblyRequest(
                agent_id="t",
                query="?",
                scope=[ContextScope.KNOWLEDGE],
                token_budget=exact_budget,
            )
        )
        assert response.truncated is False
        assert response.token_count == exact_budget

    async def test_over_budget_drops_lowest_priority(self, twin: InMemoryTwinAPI) -> None:
        hits = [
            _hit("a" * 200, 0.9),
            _hit("b" * 200, 0.5),
            _hit("c" * 200, 0.2),
        ]
        service = _StubService(hits)
        assembler = ContextAssembler(twin=twin, knowledge_service=service)  # type: ignore[arg-type]
        # Each ~200 chars ≈ 50 tokens; budget = ~120 fits two.
        request = ContextAssemblyRequest(
            agent_id="t",
            query="?",
            scope=[ContextScope.KNOWLEDGE],
            token_budget=120,
        )
        response = await assembler.assemble(request)
        assert response.truncated is True
        assert len(response.dropped_source_ids) >= 1
        # Lowest similarity fragment (`c`) must have been dropped.
        kept_contents = [f.content[:1] for f in response.fragments]
        assert "c" not in kept_contents


class TestTruncatedLog:
    async def test_emits_context_truncated_with_source_tally(self, twin: InMemoryTwinAPI) -> None:
        # Spy on the assembler's structlog logger directly — both
        # capsys and caplog are unreliable here because other test
        # modules in the full suite reconfigure structlog to a
        # non-stdout sink.
        from unittest.mock import patch

        from digital_twin.context import assembler as assembler_module

        hits = [_hit("x" * 400, 0.9), _hit("y" * 400, 0.4)]
        service = _StubService(hits)
        assembler = ContextAssembler(twin=twin, knowledge_service=service)  # type: ignore[arg-type]
        request = ContextAssemblyRequest(
            agent_id="t",
            query="?",
            scope=[ContextScope.KNOWLEDGE],
            token_budget=120,
        )
        with patch.object(
            assembler_module.logger, "info", wraps=assembler_module.logger.info
        ) as spy:
            response = await assembler.assemble(request)
        assert response.truncated is True
        truncated_calls = [
            c for c in spy.call_args_list if c.args and c.args[0] == "context_truncated"
        ]
        assert truncated_calls, [c.args for c in spy.call_args_list]
        kwargs = truncated_calls[0].kwargs
        assert kwargs.get("dropped_count") >= 1
        assert "knowledge_hit" in (kwargs.get("dropped_sources") or {})


# ---------------------------------------------------------------------------
# Direct fragment construction (regression for token_count default)
# ---------------------------------------------------------------------------


class TestFragmentRegression:
    def test_existing_fragments_still_construct(self) -> None:
        # The MET-315 / MET-316 tests construct fragments directly; the
        # MET-317 changes don't break their shape.
        frag = ContextFragment(
            content="hello",
            source_kind=ContextSourceKind.KNOWLEDGE_HIT,
            source_id="x",
            token_count=estimate_tokens("hello"),
        )
        assert frag.token_count >= 1
        assert frag.work_product_id is None
        assert frag.metadata == {}

    def test_estimate_tokens_used_by_fragment_helpers(self) -> None:
        # Round-trip a UUID-bearing fragment to assert no model
        # serialisation regression.
        wp_id = uuid4()
        frag = ContextFragment(
            content="alpha",
            source_kind=ContextSourceKind.GRAPH_NODE,
            source_id=f"work_product://{wp_id}",
            work_product_id=wp_id,
            token_count=estimate_tokens("alpha"),
        )
        round_trip = ContextFragment.model_validate_json(frag.model_dump_json())
        assert round_trip.work_product_id == wp_id
