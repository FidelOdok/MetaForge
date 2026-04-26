"""Unit tests for role-based context scoping (MET-316).

Covers:

* The role-map lookup contract (``get_role_knowledge_types`` /
  ``is_known_role``).
* The assembler's filter precedence: caller-explicit knowledge_type
  beats role narrowing; role narrowing beats no-filter.
* Hits whose ``knowledge_type`` falls outside the role allow-list are
  dropped *after* the search but before fragment creation.
* Hits with ``knowledge_type=None`` are kept (defensive — the role
  filter only screens out positively-disallowed types).
* Each of the four registered roles has the expected allow-list shape.
"""

from __future__ import annotations

from typing import Any

import pytest

from digital_twin.context import (
    ROLE_COMPLIANCE_AGENT,
    ROLE_ELECTRONICS_AGENT,
    ROLE_MECHANICAL_AGENT,
    ROLE_SIMULATION_AGENT,
    ContextAssembler,
    ContextAssemblyRequest,
    ContextScope,
    all_roles,
    get_role_knowledge_types,
    is_known_role,
)
from digital_twin.knowledge.service import IngestResult, SearchHit
from digital_twin.knowledge.types import KnowledgeType
from twin_core.api import InMemoryTwinAPI

# ---------------------------------------------------------------------------
# Role map shape
# ---------------------------------------------------------------------------


class TestRoleMap:
    def test_known_roles(self) -> None:
        assert is_known_role(ROLE_MECHANICAL_AGENT)
        assert is_known_role(ROLE_ELECTRONICS_AGENT)
        assert is_known_role(ROLE_SIMULATION_AGENT)
        assert is_known_role(ROLE_COMPLIANCE_AGENT)
        assert not is_known_role("unknown_agent")
        assert not is_known_role("")

    def test_lookup_returns_none_for_unknown(self) -> None:
        assert get_role_knowledge_types("nope") is None

    def test_mechanical_allow_list(self) -> None:
        types = get_role_knowledge_types(ROLE_MECHANICAL_AGENT)
        assert types == frozenset(
            {KnowledgeType.DESIGN_DECISION, KnowledgeType.COMPONENT, KnowledgeType.FAILURE}
        )

    def test_electronics_allow_list(self) -> None:
        types = get_role_knowledge_types(ROLE_ELECTRONICS_AGENT)
        assert types == frozenset({KnowledgeType.DESIGN_DECISION, KnowledgeType.COMPONENT})

    def test_simulation_allow_list(self) -> None:
        types = get_role_knowledge_types(ROLE_SIMULATION_AGENT)
        assert types == frozenset({KnowledgeType.SESSION, KnowledgeType.FAILURE})

    def test_compliance_allow_list(self) -> None:
        types = get_role_knowledge_types(ROLE_COMPLIANCE_AGENT)
        assert types == frozenset({KnowledgeType.CONSTRAINT, KnowledgeType.DESIGN_DECISION})

    def test_all_roles_snapshot_is_safe_to_mutate(self) -> None:
        snapshot = all_roles()
        snapshot.pop(ROLE_MECHANICAL_AGENT, None)
        # Source of truth must be unaffected.
        assert is_known_role(ROLE_MECHANICAL_AGENT)


# ---------------------------------------------------------------------------
# Assembler filter precedence
# ---------------------------------------------------------------------------


class _RecordingService:
    """Knowledge stub that records every search call and returns canned hits."""

    def __init__(self, hits: list[SearchHit]) -> None:
        self._hits = hits
        self.calls: list[dict[str, Any]] = []

    async def ingest(self, *args: Any, **kwargs: Any) -> IngestResult:  # pragma: no cover
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
        # When the caller passes a knowledge_type, simulate the
        # underlying provider applying it server-side so the assembler's
        # role-narrowing path doesn't double-filter.
        if knowledge_type is not None:
            return [h for h in self._hits if h.knowledge_type == knowledge_type]
        return list(self._hits)

    async def delete_by_source(self, source_path: str) -> int:  # pragma: no cover
        return 0

    async def health_check(self) -> dict[str, Any]:  # pragma: no cover
        return {"status": "ok"}


def _hit(content: str, kt: KnowledgeType, score: float = 0.8) -> SearchHit:
    return SearchHit(
        content=content,
        similarity_score=score,
        source_path=f"{content}.md",
        heading="H",
        chunk_index=0,
        total_chunks=1,
        metadata={},
        knowledge_type=kt,
        source_work_product_id=None,
    )


@pytest.fixture
def mixed_corpus() -> list[SearchHit]:
    return [
        _hit("decision", KnowledgeType.DESIGN_DECISION, 0.9),
        _hit("component", KnowledgeType.COMPONENT, 0.85),
        _hit("failure", KnowledgeType.FAILURE, 0.8),
        _hit("constraint", KnowledgeType.CONSTRAINT, 0.7),
        _hit("session", KnowledgeType.SESSION, 0.6),
    ]


@pytest.fixture
def twin() -> InMemoryTwinAPI:
    return InMemoryTwinAPI.create()


class TestRoleNarrowing:
    async def test_mechanical_role_drops_constraint_and_session(
        self, twin: InMemoryTwinAPI, mixed_corpus: list[SearchHit]
    ) -> None:
        service = _RecordingService(mixed_corpus)
        assembler = ContextAssembler(twin=twin, knowledge_service=service)  # type: ignore[arg-type]
        request = ContextAssemblyRequest(
            agent_id=ROLE_MECHANICAL_AGENT,
            query="?",
            scope=[ContextScope.KNOWLEDGE],
            knowledge_top_k=10,
        )
        response = await assembler.assemble(request)
        kts = {f.knowledge_type for f in response.fragments}
        assert KnowledgeType.CONSTRAINT not in kts
        assert KnowledgeType.SESSION not in kts
        assert kts == {
            KnowledgeType.DESIGN_DECISION,
            KnowledgeType.COMPONENT,
            KnowledgeType.FAILURE,
        }
        # Service was called with no type filter — role narrowing is
        # post-applied for efficiency.
        assert service.calls[0]["knowledge_type"] is None
        # Over-fetch happened (top_k * 4 = 40)
        assert service.calls[0]["top_k"] == 40

    async def test_electronics_role_keeps_only_decisions_and_components(
        self, twin: InMemoryTwinAPI, mixed_corpus: list[SearchHit]
    ) -> None:
        service = _RecordingService(mixed_corpus)
        assembler = ContextAssembler(twin=twin, knowledge_service=service)  # type: ignore[arg-type]
        request = ContextAssemblyRequest(
            agent_id=ROLE_ELECTRONICS_AGENT,
            query="?",
            scope=[ContextScope.KNOWLEDGE],
        )
        response = await assembler.assemble(request)
        kts = {f.knowledge_type for f in response.fragments}
        assert kts == {KnowledgeType.DESIGN_DECISION, KnowledgeType.COMPONENT}

    async def test_simulation_role_keeps_session_and_failure(
        self, twin: InMemoryTwinAPI, mixed_corpus: list[SearchHit]
    ) -> None:
        service = _RecordingService(mixed_corpus)
        assembler = ContextAssembler(twin=twin, knowledge_service=service)  # type: ignore[arg-type]
        request = ContextAssemblyRequest(
            agent_id=ROLE_SIMULATION_AGENT,
            query="?",
            scope=[ContextScope.KNOWLEDGE],
        )
        response = await assembler.assemble(request)
        kts = {f.knowledge_type for f in response.fragments}
        assert kts == {KnowledgeType.SESSION, KnowledgeType.FAILURE}

    async def test_compliance_role_keeps_constraint_and_decision(
        self, twin: InMemoryTwinAPI, mixed_corpus: list[SearchHit]
    ) -> None:
        service = _RecordingService(mixed_corpus)
        assembler = ContextAssembler(twin=twin, knowledge_service=service)  # type: ignore[arg-type]
        request = ContextAssemblyRequest(
            agent_id=ROLE_COMPLIANCE_AGENT,
            query="?",
            scope=[ContextScope.KNOWLEDGE],
        )
        response = await assembler.assemble(request)
        kts = {f.knowledge_type for f in response.fragments}
        assert kts == {KnowledgeType.CONSTRAINT, KnowledgeType.DESIGN_DECISION}


class TestPrecedence:
    async def test_explicit_type_wins_over_role(
        self, twin: InMemoryTwinAPI, mixed_corpus: list[SearchHit]
    ) -> None:
        service = _RecordingService(mixed_corpus)
        assembler = ContextAssembler(twin=twin, knowledge_service=service)  # type: ignore[arg-type]
        # Mechanical role normally excludes CONSTRAINT — explicit
        # CONSTRAINT must override.
        request = ContextAssemblyRequest(
            agent_id=ROLE_MECHANICAL_AGENT,
            query="?",
            scope=[ContextScope.KNOWLEDGE],
            knowledge_type=KnowledgeType.CONSTRAINT,
        )
        response = await assembler.assemble(request)
        # Service was called with the explicit filter.
        assert service.calls[0]["knowledge_type"] == KnowledgeType.CONSTRAINT
        # And no role narrowing happened post-search.
        assert all(f.knowledge_type == KnowledgeType.CONSTRAINT for f in response.fragments)
        # Over-fetch is NOT triggered when caller is explicit.
        assert service.calls[0]["top_k"] == 5

    async def test_unknown_role_applies_no_filter(
        self, twin: InMemoryTwinAPI, mixed_corpus: list[SearchHit]
    ) -> None:
        service = _RecordingService(mixed_corpus)
        assembler = ContextAssembler(twin=twin, knowledge_service=service)  # type: ignore[arg-type]
        request = ContextAssemblyRequest(
            agent_id="some_unrecognised_agent",
            query="?",
            scope=[ContextScope.KNOWLEDGE],
            knowledge_top_k=10,
        )
        response = await assembler.assemble(request)
        kts = {f.knowledge_type for f in response.fragments}
        # All five corpus types come through — unknown agent_id ⇒ no filter.
        assert len(kts) == 5
        assert service.calls[0]["knowledge_type"] is None
        # No over-fetch when no role filter applies.
        assert service.calls[0]["top_k"] == 10


class TestEdgeCases:
    async def test_hit_with_none_knowledge_type_is_kept(self, twin: InMemoryTwinAPI) -> None:
        # Defensive: hits without a typed annotation should not be
        # silently dropped by role narrowing.
        untyped = SearchHit(
            content="no-type hit",
            similarity_score=0.5,
            source_path="x.md",
            heading=None,
            chunk_index=0,
            total_chunks=1,
            metadata={},
            knowledge_type=None,
            source_work_product_id=None,
        )
        service = _RecordingService([untyped])
        assembler = ContextAssembler(twin=twin, knowledge_service=service)  # type: ignore[arg-type]
        request = ContextAssemblyRequest(
            agent_id=ROLE_MECHANICAL_AGENT,
            query="?",
            scope=[ContextScope.KNOWLEDGE],
        )
        response = await assembler.assemble(request)
        assert len(response.fragments) == 1
        assert response.fragments[0].content == "no-type hit"

    async def test_role_narrowing_respects_top_k_cap(self, twin: InMemoryTwinAPI) -> None:
        many = [_hit(f"d{i}", KnowledgeType.DESIGN_DECISION, 0.9 - i * 0.01) for i in range(20)]
        service = _RecordingService(many)
        assembler = ContextAssembler(twin=twin, knowledge_service=service)  # type: ignore[arg-type]
        request = ContextAssemblyRequest(
            agent_id=ROLE_MECHANICAL_AGENT,
            query="?",
            scope=[ContextScope.KNOWLEDGE],
            knowledge_top_k=5,
        )
        response = await assembler.assemble(request)
        # The role allows DESIGN_DECISION and we have 20 hits — final
        # response must still cap at the caller's top_k.
        assert len(response.fragments) == 5
