"""Unit tests for the context assembly protocol (MET-315).

Cover the contract surface without any live backend:

* Models construct, serialise round-trip, and the ``includes_*``
  shortcuts on ``ContextAssemblyRequest`` route correctly.
* The assembler delegates to the right collectors based on scope and
  always tags every fragment with a ``source_id``.
* Budget enforcement drops the lowest-priority fragments first and
  reports them in ``dropped_source_ids``.
* Empty stores produce a clean empty response (no exception).
* Failures in one collector do not blow up the whole assembly.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest

from digital_twin.context import (
    ContextAssembler,
    ContextAssemblyRequest,
    ContextFragment,
    ContextScope,
    ContextSourceKind,
)
from digital_twin.context.models import estimate_tokens
from digital_twin.knowledge.service import IngestResult, SearchHit
from digital_twin.knowledge.types import KnowledgeType
from twin_core.api import InMemoryTwinAPI
from twin_core.models.enums import WorkProductType
from twin_core.models.work_product import WorkProduct

# ---------------------------------------------------------------------------
# Shared doubles
# ---------------------------------------------------------------------------


class _FakeKnowledgeService:
    """Stub that returns a configurable list of hits."""

    def __init__(self, hits: list[SearchHit] | None = None, fail: bool = False) -> None:
        self.hits = hits or []
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    async def ingest(self, *args: Any, **kwargs: Any) -> IngestResult:  # pragma: no cover — unused
        return IngestResult(entry_ids=[], chunks_indexed=0, source_path="")

    async def search(
        self,
        query: str,
        top_k: int = 5,
        knowledge_type: KnowledgeType | None = None,
        filters: dict[str, Any] | None = None,
    ) -> list[SearchHit]:
        self.calls.append(
            {"query": query, "top_k": top_k, "knowledge_type": knowledge_type, "filters": filters}
        )
        if self.fail:
            raise RuntimeError("simulated knowledge failure")
        return self.hits

    async def delete_by_source(self, source_path: str) -> int:  # pragma: no cover
        return 0

    async def health_check(self) -> dict[str, Any]:  # pragma: no cover
        return {"status": "ok"}


def _make_hit(content: str, score: float, source_path: str | None = "doc.md") -> SearchHit:
    return SearchHit(
        content=content,
        similarity_score=score,
        source_path=source_path,
        heading="Decision",
        chunk_index=0,
        total_chunks=1,
        metadata={},
        knowledge_type=KnowledgeType.DESIGN_DECISION,
        source_work_product_id=None,
    )


def _make_work_product(name: str = "bracket", wp_id: UUID | None = None) -> WorkProduct:
    return WorkProduct(
        id=wp_id or uuid4(),
        name=name,
        type=WorkProductType.CAD_MODEL,
        domain="mechanical",
        file_path=f"cad/{name}.step",
        content_hash="abc123",
        format="step",
        created_by="test",
    )


@pytest.fixture
def twin() -> InMemoryTwinAPI:
    return InMemoryTwinAPI.create()


@pytest.fixture
def knowledge() -> _FakeKnowledgeService:
    return _FakeKnowledgeService()


@pytest.fixture
def assembler(twin: InMemoryTwinAPI, knowledge: _FakeKnowledgeService) -> ContextAssembler:
    return ContextAssembler(twin=twin, knowledge_service=knowledge)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class TestModels:
    def test_request_includes_helpers_default_all(self) -> None:
        req = ContextAssemblyRequest(agent_id="mech_agent", query="bracket?")
        assert req.includes_knowledge is True
        assert req.includes_graph is True
        assert req.includes_work_product is True

    def test_request_includes_helpers_specific_scope(self) -> None:
        req = ContextAssemblyRequest(
            agent_id="ee", query="net list?", scope=[ContextScope.KNOWLEDGE]
        )
        assert req.includes_knowledge is True
        assert req.includes_graph is False
        assert req.includes_work_product is False

    def test_estimate_tokens_deterministic(self) -> None:
        # MET-317 swapped the char heuristic for tiktoken — empty stays
        # 0, non-empty stays >= 1. Exact BPE counts are validated in
        # ``tests/unit/test_token_budget.py``.
        assert estimate_tokens("") == 0
        assert estimate_tokens("a") >= 1
        assert estimate_tokens("a" * 8) >= 1

    def test_fragment_round_trip(self) -> None:
        wp_id = uuid4()
        frag = ContextFragment(
            content="hello",
            source_kind=ContextSourceKind.KNOWLEDGE_HIT,
            source_id="doc.md",
            source_path="doc.md",
            similarity_score=0.7,
            knowledge_type=KnowledgeType.DESIGN_DECISION,
            work_product_id=wp_id,
            token_count=2,
        )
        round_trip = ContextFragment.model_validate_json(frag.model_dump_json())
        assert round_trip.work_product_id == wp_id
        assert round_trip.knowledge_type == KnowledgeType.DESIGN_DECISION


# ---------------------------------------------------------------------------
# Assembler — collectors
# ---------------------------------------------------------------------------


class TestKnowledgeCollector:
    async def test_knowledge_only_returns_search_hits(
        self, assembler: ContextAssembler, knowledge: _FakeKnowledgeService
    ) -> None:
        knowledge.hits = [_make_hit("alpha", 0.9), _make_hit("beta", 0.4)]
        request = ContextAssemblyRequest(
            agent_id="mech",
            query="bracket?",
            scope=[ContextScope.KNOWLEDGE],
        )
        response = await assembler.assemble(request)
        assert len(response.fragments) == 2
        assert {f.content for f in response.fragments} == {"alpha", "beta"}
        assert all(f.source_kind == ContextSourceKind.KNOWLEDGE_HIT for f in response.fragments)
        # Ranked by similarity descending
        assert response.fragments[0].content == "alpha"
        # Search call delegated correctly
        assert knowledge.calls[0]["query"] == "bracket?"

    async def test_skip_when_no_query(
        self, assembler: ContextAssembler, knowledge: _FakeKnowledgeService
    ) -> None:
        knowledge.hits = [_make_hit("anything", 0.8)]
        request = ContextAssemblyRequest(
            agent_id="mech",
            scope=[ContextScope.KNOWLEDGE],
        )
        response = await assembler.assemble(request)
        assert response.fragments == []
        assert knowledge.calls == []

    async def test_search_failure_does_not_blow_up(
        self, assembler: ContextAssembler, knowledge: _FakeKnowledgeService
    ) -> None:
        knowledge.fail = True
        request = ContextAssemblyRequest(
            agent_id="mech",
            query="bracket?",
            scope=[ContextScope.KNOWLEDGE],
        )
        response = await assembler.assemble(request)
        assert response.fragments == []
        assert response.token_count == 0


class TestWorkProductCollector:
    async def test_work_product_pivot_emits_graph_node(
        self, assembler: ContextAssembler, twin: InMemoryTwinAPI
    ) -> None:
        wp = _make_work_product()
        await twin.create_work_product(wp)
        request = ContextAssemblyRequest(
            agent_id="mech",
            scope=[ContextScope.WORK_PRODUCT],
            work_product_id=wp.id,
        )
        response = await assembler.assemble(request)
        assert len(response.fragments) == 1
        frag = response.fragments[0]
        assert frag.source_kind == ContextSourceKind.GRAPH_NODE
        assert frag.source_id == f"work_product://{wp.id}"
        assert frag.work_product_id == wp.id
        assert "bracket" in frag.content

    async def test_unknown_work_product_returns_empty(self, assembler: ContextAssembler) -> None:
        request = ContextAssemblyRequest(
            agent_id="mech",
            scope=[ContextScope.WORK_PRODUCT],
            work_product_id=uuid4(),
        )
        response = await assembler.assemble(request)
        assert response.fragments == []


# ---------------------------------------------------------------------------
# Mixed-source orchestration
# ---------------------------------------------------------------------------


class TestMixedSources:
    async def test_all_scope_combines_knowledge_and_pivot(
        self,
        assembler: ContextAssembler,
        twin: InMemoryTwinAPI,
        knowledge: _FakeKnowledgeService,
    ) -> None:
        wp = _make_work_product()
        await twin.create_work_product(wp)
        knowledge.hits = [_make_hit("decision body", 0.8)]
        request = ContextAssemblyRequest(
            agent_id="mech",
            query="bracket?",
            scope=[ContextScope.ALL],
            work_product_id=wp.id,
            graph_depth=0,  # skip the subgraph collector for this assertion
        )
        response = await assembler.assemble(request)
        kinds = {f.source_kind for f in response.fragments}
        assert ContextSourceKind.KNOWLEDGE_HIT in kinds
        assert ContextSourceKind.GRAPH_NODE in kinds
        # Sources tally correctly
        assert response.sources["knowledge_hit"] == 1
        assert response.sources["graph_node"] == 1


# ---------------------------------------------------------------------------
# Budget enforcement
# ---------------------------------------------------------------------------


class TestBudget:
    async def test_budget_drops_low_priority_fragments(
        self, assembler: ContextAssembler, knowledge: _FakeKnowledgeService
    ) -> None:
        # Three hits, ~ token cost 25 each (100 chars each).
        knowledge.hits = [
            _make_hit("a" * 100, 0.9),
            _make_hit("b" * 100, 0.5),
            _make_hit("c" * 100, 0.2),
        ]
        request = ContextAssemblyRequest(
            agent_id="mech",
            query="?",
            scope=[ContextScope.KNOWLEDGE],
            token_budget=50,  # fits two but not three
        )
        response = await assembler.assemble(request)
        assert response.truncated is True
        assert len(response.fragments) == 2
        assert len(response.dropped_source_ids) == 1
        # Lowest-similarity fragment dropped
        assert response.fragments[0].similarity_score == 0.9
        assert response.fragments[-1].similarity_score == 0.5

    async def test_zero_match_yields_clean_empty_response(
        self, assembler: ContextAssembler, knowledge: _FakeKnowledgeService
    ) -> None:
        request = ContextAssemblyRequest(
            agent_id="mech",
            query="anything",
            scope=[ContextScope.KNOWLEDGE],
        )
        response = await assembler.assemble(request)
        assert response.fragments == []
        assert response.token_count == 0
        assert response.truncated is False
        assert response.metadata["agent_id"] == "mech"


# ---------------------------------------------------------------------------
# Attribution invariant
# ---------------------------------------------------------------------------


class TestAttribution:
    async def test_every_fragment_has_source_id(
        self,
        assembler: ContextAssembler,
        twin: InMemoryTwinAPI,
        knowledge: _FakeKnowledgeService,
    ) -> None:
        wp = _make_work_product()
        await twin.create_work_product(wp)
        knowledge.hits = [_make_hit("alpha", 0.9), _make_hit("beta", 0.6)]
        request = ContextAssemblyRequest(
            agent_id="mech",
            query="?",
            scope=[ContextScope.ALL],
            work_product_id=wp.id,
            graph_depth=0,
        )
        response = await assembler.assemble(request)
        assert response.fragments
        # Every fragment must declare its origin and a non-empty source_id.
        assert all(f.source_id for f in response.fragments)
        assert all(f.source_kind in ContextSourceKind for f in response.fragments)
