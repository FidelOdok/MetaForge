"""Unit tests for the knowledge pipeline (MET-183).

Covers:
- KnowledgeType enum values
- KnowledgeEntry model validation
- SearchResult and SearchQuery models
- TextChunker: basic chunking, overlap, short text, empty text
- Template rendering: all types, missing keys, generic dispatcher
- Knowledge search endpoint: valid query, type filter, empty results
- Integration with existing KnowledgeStore (mock pgvector via InMemoryKnowledgeStore)
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from digital_twin.knowledge.chunker import TextChunker
from digital_twin.knowledge.models import SearchQuery, SearchResult
from digital_twin.knowledge.store import (
    InMemoryKnowledgeStore,
    KnowledgeEntry,
    KnowledgeType,
)
from digital_twin.knowledge.templates import (
    render_component_selection,
    render_constraint_rationale,
    render_design_decision,
    render_failure_mode,
    render_session_summary,
    render_template,
)

# ---------------------------------------------------------------------------
# KnowledgeType enum
# ---------------------------------------------------------------------------


class TestKnowledgeType:
    """Tests for KnowledgeType enum values."""

    def test_enum_values(self) -> None:
        assert KnowledgeType.DESIGN_DECISION == "design_decision"
        assert KnowledgeType.COMPONENT == "component"
        assert KnowledgeType.FAILURE == "failure"
        assert KnowledgeType.CONSTRAINT == "constraint"
        assert KnowledgeType.SESSION == "session"

    def test_enum_count(self) -> None:
        assert len(KnowledgeType) == 5

    def test_enum_from_value(self) -> None:
        assert KnowledgeType("design_decision") is KnowledgeType.DESIGN_DECISION


# ---------------------------------------------------------------------------
# KnowledgeEntry model
# ---------------------------------------------------------------------------


class TestKnowledgeEntry:
    """Tests for KnowledgeEntry Pydantic model."""

    def test_valid_entry(self) -> None:
        entry = KnowledgeEntry(
            content="Test content",
            knowledge_type=KnowledgeType.DESIGN_DECISION,
        )
        assert entry.content == "Test content"
        assert entry.knowledge_type == KnowledgeType.DESIGN_DECISION
        assert isinstance(entry.id, UUID)
        assert entry.embedding == []
        assert entry.metadata == {}
        assert entry.source_work_product_id is None
        assert isinstance(entry.created_at, datetime)

    def test_entry_with_all_fields(self) -> None:
        uid = uuid4()
        work_product_id = uuid4()
        now = datetime.now(UTC)
        entry = KnowledgeEntry(
            id=uid,
            content="Full entry",
            embedding=[0.1, 0.2, 0.3],
            knowledge_type=KnowledgeType.COMPONENT,
            metadata={"key": "value"},
            source_work_product_id=work_product_id,
            created_at=now,
        )
        assert entry.id == uid
        assert entry.embedding == [0.1, 0.2, 0.3]
        assert entry.source_work_product_id == work_product_id
        assert entry.created_at == now

    def test_entry_empty_content_rejected(self) -> None:
        with pytest.raises(ValidationError):
            KnowledgeEntry(
                content="",
                knowledge_type=KnowledgeType.SESSION,
            )


# ---------------------------------------------------------------------------
# SearchResult model
# ---------------------------------------------------------------------------


class TestSearchResult:
    """Tests for SearchResult model."""

    def test_valid_search_result(self) -> None:
        entry = KnowledgeEntry(
            content="test",
            knowledge_type=KnowledgeType.SESSION,
        )
        result = SearchResult(entry=entry, similarity_score=0.95)
        assert result.similarity_score == 0.95
        assert result.entry.content == "test"

    def test_score_bounds(self) -> None:
        entry = KnowledgeEntry(
            content="test",
            knowledge_type=KnowledgeType.SESSION,
        )
        # Score below 0 should fail
        with pytest.raises(ValidationError):
            SearchResult(entry=entry, similarity_score=-0.1)
        # Score above 1 should fail
        with pytest.raises(ValidationError):
            SearchResult(entry=entry, similarity_score=1.1)

    def test_boundary_scores(self) -> None:
        entry = KnowledgeEntry(
            content="test",
            knowledge_type=KnowledgeType.SESSION,
        )
        r0 = SearchResult(entry=entry, similarity_score=0.0)
        r1 = SearchResult(entry=entry, similarity_score=1.0)
        assert r0.similarity_score == 0.0
        assert r1.similarity_score == 1.0


# ---------------------------------------------------------------------------
# SearchQuery model
# ---------------------------------------------------------------------------


class TestSearchQuery:
    """Tests for SearchQuery model."""

    def test_minimal_query(self) -> None:
        q = SearchQuery(query="hello world")
        assert q.query == "hello world"
        assert q.knowledge_type is None
        assert q.limit == 10
        assert q.metadata_filter is None
        assert q.source_work_product_id is None

    def test_query_with_type_filter(self) -> None:
        q = SearchQuery(
            query="stress analysis",
            knowledge_type=KnowledgeType.DESIGN_DECISION,
            limit=20,
        )
        assert q.knowledge_type == KnowledgeType.DESIGN_DECISION
        assert q.limit == 20

    def test_empty_query_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SearchQuery(query="")


# ---------------------------------------------------------------------------
# TextChunker
# ---------------------------------------------------------------------------


class TestTextChunker:
    """Tests for the TextChunker class."""

    def test_basic_chunking(self) -> None:
        chunker = TextChunker(chunk_size=5, overlap=2)
        text = "a b c d e f g h i j"
        chunks = chunker.chunk_text(text)
        assert len(chunks) >= 2
        # First chunk should have first 5 words
        assert chunks[0] == "a b c d e"

    def test_overlap(self) -> None:
        chunker = TextChunker(chunk_size=5, overlap=2)
        text = "one two three four five six seven eight"
        chunks = chunker.chunk_text(text)
        # With chunk_size=5, overlap=2, step=3
        # Chunk 0: words[0:5] = "one two three four five"
        # Chunk 1: words[3:8] = "four five six seven eight"
        # Chunk 2: words[6:8] = "seven eight" (tail)
        assert len(chunks) == 3
        # Verify overlap: "four five" appears in chunks 0 and 1
        assert "four five" in chunks[0]
        assert "four five" in chunks[1]
        # "seven eight" appears in chunks 1 and 2
        assert "seven eight" in chunks[1]
        assert "seven eight" in chunks[2]

    def test_short_text_single_chunk(self) -> None:
        chunker = TextChunker(chunk_size=512, overlap=64)
        text = "short text"
        chunks = chunker.chunk_text(text)
        assert len(chunks) == 1
        assert chunks[0] == "short text"

    def test_empty_text(self) -> None:
        chunker = TextChunker(chunk_size=512, overlap=64)
        assert chunker.chunk_text("") == []
        assert chunker.chunk_text("   ") == []

    def test_chunk_document_metadata(self) -> None:
        chunker = TextChunker(chunk_size=3, overlap=1)
        text = "a b c d e"
        meta = {"source": "test", "doc_id": "123"}
        docs = chunker.chunk_document(text, metadata=meta)
        assert len(docs) >= 2
        for doc in docs:
            assert "content" in doc
            assert "chunk_index" in doc
            assert "total_chunks" in doc
            assert doc["source"] == "test"
            assert doc["doc_id"] == "123"
        assert docs[0]["chunk_index"] == 0
        assert docs[-1]["chunk_index"] == len(docs) - 1

    def test_chunk_document_no_metadata(self) -> None:
        chunker = TextChunker(chunk_size=3, overlap=1)
        docs = chunker.chunk_document("a b c d e")
        for doc in docs:
            assert "content" in doc
            assert "chunk_index" in doc

    def test_invalid_chunk_size(self) -> None:
        with pytest.raises(ValueError, match="chunk_size must be positive"):
            TextChunker(chunk_size=0, overlap=0)

    def test_overlap_exceeds_chunk_size(self) -> None:
        with pytest.raises(ValueError, match="overlap.*must be less than"):
            TextChunker(chunk_size=5, overlap=5)

    def test_default_parameters(self) -> None:
        chunker = TextChunker()
        assert chunker.chunk_size == 512
        assert chunker.overlap == 64


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


class TestTemplates:
    """Tests for template rendering functions."""

    def test_render_design_decision(self) -> None:
        ctx: dict[str, object] = {
            "title": "Use STM32F4",
            "rationale": "Best price-performance for our needs",
            "alternatives": "ESP32, nRF52840",
            "outcome": "STM32F407 selected",
        }
        result = render_design_decision(ctx)
        assert "Design Decision: Use STM32F4" in result
        assert "Rationale: Best price-performance" in result
        assert "Alternatives Considered: ESP32" in result
        assert "Outcome: STM32F407 selected" in result

    def test_render_session_summary(self) -> None:
        ctx: dict[str, object] = {
            "session_id": "sess-001",
            "summary": "Reviewed PCB layout",
            "decisions": "Moved decoupling caps closer",
        }
        result = render_session_summary(ctx)
        assert "Session Summary (ID: sess-001)" in result
        assert "Reviewed PCB layout" in result

    def test_render_component_selection(self) -> None:
        ctx: dict[str, object] = {
            "component": "LM7805",
            "reason": "Wide availability",
            "specifications": "5V, 1A output",
        }
        result = render_component_selection(ctx)
        assert "Component Selection: LM7805" in result
        assert "Wide availability" in result

    def test_render_failure_mode(self) -> None:
        ctx: dict[str, object] = {
            "failure": "Thermal runaway",
            "severity": "Critical",
            "mitigation": "Add thermal shutdown circuit",
        }
        result = render_failure_mode(ctx)
        assert "Failure Mode: Thermal runaway" in result
        assert "Severity: Critical" in result

    def test_render_constraint_rationale(self) -> None:
        ctx: dict[str, object] = {
            "constraint": "Max current draw 500mA",
            "rationale": "USB bus power limit",
            "domain": "electronics",
        }
        result = render_constraint_rationale(ctx)
        assert "Constraint: Max current draw 500mA" in result
        assert "USB bus power limit" in result

    def test_render_template_dispatch(self) -> None:
        ctx: dict[str, object] = {"title": "Test", "rationale": "Because"}
        result = render_template(KnowledgeType.DESIGN_DECISION, ctx)
        assert "Design Decision: Test" in result

    def test_render_template_all_types(self) -> None:
        """Every KnowledgeType has a registered template."""
        for kt in KnowledgeType:
            result = render_template(kt, {})
            assert isinstance(result, str)
            assert len(result) > 0

    def test_render_design_decision_missing_keys(self) -> None:
        """Template renders gracefully with empty context."""
        result = render_design_decision({})
        assert "Design Decision: Untitled Decision" in result

    def test_render_session_summary_missing_keys(self) -> None:
        result = render_session_summary({})
        assert "Session Summary (ID: unknown)" in result


# ---------------------------------------------------------------------------
# InMemoryKnowledgeStore integration
# ---------------------------------------------------------------------------


class TestInMemoryKnowledgeStoreIntegration:
    """Integration tests using InMemoryKnowledgeStore."""

    @pytest.fixture()
    def store(self) -> InMemoryKnowledgeStore:
        return InMemoryKnowledgeStore()

    @pytest.mark.asyncio()
    async def test_store_and_search(self, store: InMemoryKnowledgeStore) -> None:
        entry = KnowledgeEntry(
            content="Stress analysis passed for bracket",
            embedding=[1.0, 0.0, 0.0],
            knowledge_type=KnowledgeType.DESIGN_DECISION,
        )
        await store.store(entry)
        results = await store.search(
            embedding=[1.0, 0.0, 0.0],
            limit=5,
        )
        assert len(results) == 1
        assert results[0].id == entry.id

    @pytest.mark.asyncio()
    async def test_search_with_type_filter(self, store: InMemoryKnowledgeStore) -> None:
        e1 = KnowledgeEntry(
            content="Decision A",
            embedding=[1.0, 0.0],
            knowledge_type=KnowledgeType.DESIGN_DECISION,
        )
        e2 = KnowledgeEntry(
            content="Component B",
            embedding=[1.0, 0.0],
            knowledge_type=KnowledgeType.COMPONENT,
        )
        await store.store(e1)
        await store.store(e2)
        results = await store.search(
            embedding=[1.0, 0.0],
            knowledge_type=KnowledgeType.COMPONENT,
            limit=10,
        )
        assert len(results) == 1
        assert results[0].knowledge_type == KnowledgeType.COMPONENT

    @pytest.mark.asyncio()
    async def test_search_empty_store(self, store: InMemoryKnowledgeStore) -> None:
        results = await store.search(embedding=[1.0, 0.0, 0.0], limit=5)
        assert results == []

    @pytest.mark.asyncio()
    async def test_store_idempotent(self, store: InMemoryKnowledgeStore) -> None:
        entry = KnowledgeEntry(
            content="Idempotent entry",
            embedding=[0.5, 0.5],
            knowledge_type=KnowledgeType.SESSION,
        )
        await store.store(entry)
        await store.store(entry)  # Same ID, should overwrite
        entries = await store.list()
        assert len(entries) == 1


# ---------------------------------------------------------------------------
# Knowledge search endpoint (mocked)
# ---------------------------------------------------------------------------


class TestKnowledgeSearchEndpoint:
    """Tests for the search endpoint via FastAPI TestClient."""

    @pytest.fixture()
    def client(self) -> Any:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from api_gateway.knowledge.routes import router

        app = FastAPI()
        app.include_router(router)

        # Wire up mock services
        mock_store = AsyncMock()
        mock_embedding = AsyncMock()

        app.state.knowledge_store = mock_store
        app.state.embedding_service = mock_embedding

        mock_embedding.embed = AsyncMock(return_value=[0.1, 0.2, 0.3])
        mock_store.search = AsyncMock(return_value=[])
        mock_store.get = AsyncMock(return_value=None)

        return TestClient(app)

    def test_search_valid_query(self, client: Any) -> None:
        resp = client.get("/api/v1/knowledge/search?query=test")
        assert resp.status_code == 200
        data = resp.json()
        assert data["query"] == "test"
        assert data["totalFound"] == 0
        assert data["results"] == []

    def test_search_with_type_filter(self, client: Any) -> None:
        resp = client.get(
            "/api/v1/knowledge/search?query=stress&knowledgeType=design_decision&limit=3"
        )
        assert resp.status_code == 200

    def test_search_missing_query(self, client: Any) -> None:
        resp = client.get("/api/v1/knowledge/search")
        assert resp.status_code == 422  # Validation error

    def test_get_entry_not_found(self, client: Any) -> None:
        uid = str(uuid4())
        resp = client.get(f"/api/v1/knowledge/{uid}")
        assert resp.status_code == 404
