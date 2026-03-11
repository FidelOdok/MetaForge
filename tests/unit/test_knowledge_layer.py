"""Comprehensive tests for the Knowledge Layer (MET-200).

Covers:
- InMemoryKnowledgeStore CRUD + search
- LocalEmbeddingService (mocked sentence-transformers)
- KnowledgeConsumer event handling
- RetrieveKnowledgeHandler
- IngestKnowledgeHandler
- Knowledge API routes (TestClient)
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from digital_twin.knowledge.consumer import KnowledgeConsumer
from digital_twin.knowledge.embedding_service import (
    LocalEmbeddingService,
    create_embedding_service,
)
from digital_twin.knowledge.store import (
    InMemoryKnowledgeStore,
    KnowledgeEntry,
    KnowledgeType,
    _cosine_similarity,
)
from domain_agents.shared.skills.ingest_knowledge.handler import IngestKnowledgeHandler
from domain_agents.shared.skills.ingest_knowledge.schema import IngestKnowledgeInput
from domain_agents.shared.skills.retrieve_knowledge.handler import RetrieveKnowledgeHandler
from domain_agents.shared.skills.retrieve_knowledge.schema import RetrieveKnowledgeInput
from orchestrator.event_bus.events import Event, EventType

# ============================================================================
# InMemoryKnowledgeStore tests
# ============================================================================


class TestInMemoryKnowledgeStore:
    """Test InMemoryKnowledgeStore CRUD and search."""

    @pytest.fixture()
    def store(self) -> InMemoryKnowledgeStore:
        return InMemoryKnowledgeStore()

    async def test_store_and_get(self, store: InMemoryKnowledgeStore) -> None:
        entry = KnowledgeEntry(
            content="Test content",
            embedding=[1.0, 0.0, 0.0],
            knowledge_type=KnowledgeType.DESIGN_DECISION,
        )
        stored = await store.store(entry)
        assert stored.id == entry.id

        retrieved = await store.get(entry.id)
        assert retrieved is not None
        assert retrieved.content == "Test content"

    async def test_get_nonexistent(self, store: InMemoryKnowledgeStore) -> None:
        result = await store.get(uuid4())
        assert result is None

    async def test_delete(self, store: InMemoryKnowledgeStore) -> None:
        entry = KnowledgeEntry(
            content="To delete",
            embedding=[1.0, 0.0, 0.0],
            knowledge_type=KnowledgeType.FAILURE,
        )
        await store.store(entry)
        assert await store.delete(entry.id) is True
        assert await store.get(entry.id) is None
        assert await store.delete(entry.id) is False

    async def test_list_all(self, store: InMemoryKnowledgeStore) -> None:
        for i in range(3):
            await store.store(
                KnowledgeEntry(
                    content=f"Entry {i}",
                    embedding=[float(i), 0.0, 0.0],
                    knowledge_type=KnowledgeType.SESSION,
                )
            )
        results = await store.list()
        assert len(results) == 3

    async def test_list_filtered_by_type(self, store: InMemoryKnowledgeStore) -> None:
        await store.store(
            KnowledgeEntry(
                content="Decision",
                embedding=[1.0, 0.0, 0.0],
                knowledge_type=KnowledgeType.DESIGN_DECISION,
            )
        )
        await store.store(
            KnowledgeEntry(
                content="Component",
                embedding=[0.0, 1.0, 0.0],
                knowledge_type=KnowledgeType.COMPONENT,
            )
        )
        results = await store.list(knowledge_type=KnowledgeType.COMPONENT)
        assert len(results) == 1
        assert results[0].content == "Component"

    async def test_list_with_limit(self, store: InMemoryKnowledgeStore) -> None:
        for i in range(5):
            await store.store(
                KnowledgeEntry(
                    content=f"Entry {i}",
                    embedding=[float(i), 0.0, 0.0],
                    knowledge_type=KnowledgeType.SESSION,
                )
            )
        results = await store.list(limit=2)
        assert len(results) == 2

    async def test_search_cosine_similarity(self, store: InMemoryKnowledgeStore) -> None:
        await store.store(
            KnowledgeEntry(
                content="Close match",
                embedding=[1.0, 0.0, 0.0],
                knowledge_type=KnowledgeType.DESIGN_DECISION,
            )
        )
        await store.store(
            KnowledgeEntry(
                content="Far match",
                embedding=[0.0, 1.0, 0.0],
                knowledge_type=KnowledgeType.DESIGN_DECISION,
            )
        )
        results = await store.search(embedding=[1.0, 0.0, 0.0], limit=2)
        assert len(results) == 2
        assert results[0].content == "Close match"

    async def test_search_with_type_filter(self, store: InMemoryKnowledgeStore) -> None:
        await store.store(
            KnowledgeEntry(
                content="Decision",
                embedding=[1.0, 0.0, 0.0],
                knowledge_type=KnowledgeType.DESIGN_DECISION,
            )
        )
        await store.store(
            KnowledgeEntry(
                content="Component",
                embedding=[1.0, 0.1, 0.0],
                knowledge_type=KnowledgeType.COMPONENT,
            )
        )
        results = await store.search(
            embedding=[1.0, 0.0, 0.0],
            knowledge_type=KnowledgeType.COMPONENT,
            limit=5,
        )
        assert len(results) == 1
        assert results[0].content == "Component"

    async def test_search_empty_store(self, store: InMemoryKnowledgeStore) -> None:
        results = await store.search(embedding=[1.0, 0.0, 0.0])
        assert results == []


class TestCosinesSimilarity:
    """Test the cosine similarity helper."""

    def test_identical_vectors(self) -> None:
        assert _cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)

    def test_orthogonal_vectors(self) -> None:
        assert _cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_empty_vectors(self) -> None:
        assert _cosine_similarity([], []) == 0.0

    def test_different_lengths(self) -> None:
        assert _cosine_similarity([1.0], [1.0, 0.0]) == 0.0


# ============================================================================
# LocalEmbeddingService tests
# ============================================================================


class TestLocalEmbeddingService:
    """Test the local embedding service with mocked sentence-transformers."""

    async def test_embed_fallback_when_not_installed(self) -> None:
        svc = LocalEmbeddingService()
        # Force unavailable
        svc._available = False
        result = await svc.embed("test text")
        assert len(result) == 384
        assert all(v == 0.0 for v in result)

    async def test_embed_batch_fallback(self) -> None:
        svc = LocalEmbeddingService()
        svc._available = False
        results = await svc.embed_batch(["a", "b", "c"])
        assert len(results) == 3
        assert all(len(r) == 384 for r in results)

    async def test_embed_with_model(self) -> None:
        svc = LocalEmbeddingService()
        mock_model = MagicMock()
        # Simulate a numpy-like array with a tolist() method
        mock_array = MagicMock()
        mock_array.tolist.return_value = [0.1, 0.2, 0.3]
        mock_model.encode.return_value = mock_array
        svc._model = mock_model
        svc._available = True

        result = await svc.embed("hello")
        mock_model.encode.assert_called_once_with("hello", convert_to_numpy=True)
        assert result == [0.1, 0.2, 0.3]


class TestEmbeddingFactory:
    """Test the embedding service factory."""

    def test_create_local(self) -> None:
        svc = create_embedding_service("local")
        assert isinstance(svc, LocalEmbeddingService)

    def test_create_unknown_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown embedding provider"):
            create_embedding_service("unknown")


# ============================================================================
# KnowledgeConsumer tests
# ============================================================================


class TestKnowledgeConsumer:
    """Test event-driven knowledge indexing."""

    @pytest.fixture()
    def store(self) -> InMemoryKnowledgeStore:
        return InMemoryKnowledgeStore()

    @pytest.fixture()
    def mock_embedding(self) -> AsyncMock:
        svc = AsyncMock()
        svc.embed.return_value = [0.5, 0.5, 0.0]
        svc.embed_batch.return_value = [[0.5, 0.5, 0.0]]
        return svc

    @pytest.fixture()
    def consumer(
        self, store: InMemoryKnowledgeStore, mock_embedding: AsyncMock
    ) -> KnowledgeConsumer:
        return KnowledgeConsumer(store=store, embedding_service=mock_embedding)

    def test_subscriber_id(self, consumer: KnowledgeConsumer) -> None:
        assert consumer.subscriber_id == "knowledge_consumer"

    def test_event_types(self, consumer: KnowledgeConsumer) -> None:
        assert consumer.event_types == {
            EventType.ARTIFACT_CREATED,
            EventType.ARTIFACT_UPDATED,
        }

    async def test_on_event_indexes_artifact(
        self,
        consumer: KnowledgeConsumer,
        store: InMemoryKnowledgeStore,
    ) -> None:
        event = Event(
            id=str(uuid4()),
            type=EventType.ARTIFACT_CREATED,
            timestamp=datetime.now(UTC).isoformat(),
            source="test",
            data={
                "content": "Design decision: use aluminum",
                "artifact_type": "design_decision",
                "artifact_id": uuid4(),
            },
        )
        await consumer.on_event(event)
        entries = await store.list()
        assert len(entries) == 1
        assert entries[0].content == "Design decision: use aluminum"
        assert entries[0].knowledge_type == KnowledgeType.DESIGN_DECISION

    async def test_on_event_skips_empty_content(
        self,
        consumer: KnowledgeConsumer,
        store: InMemoryKnowledgeStore,
    ) -> None:
        event = Event(
            id=str(uuid4()),
            type=EventType.ARTIFACT_CREATED,
            timestamp=datetime.now(UTC).isoformat(),
            source="test",
            data={},  # No content
        )
        await consumer.on_event(event)
        entries = await store.list()
        assert len(entries) == 0

    async def test_ingest_batch(
        self,
        consumer: KnowledgeConsumer,
        store: InMemoryKnowledgeStore,
    ) -> None:
        items = [
            {"content": "Item 1", "knowledge_type": "session"},
            {"content": "Item 2", "knowledge_type": "component"},
        ]
        consumer._embedding.embed_batch.return_value = [
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
        ]
        count = await consumer.ingest_batch(items)
        assert count == 2
        entries = await store.list()
        assert len(entries) == 2


# ============================================================================
# RetrieveKnowledgeHandler tests
# ============================================================================


class TestRetrieveKnowledgeHandler:
    """Test the retrieve_knowledge skill handler."""

    @pytest.fixture()
    def mock_context(self) -> MagicMock:
        ctx = MagicMock()
        ctx.logger = MagicMock()
        ctx.logger.bind = MagicMock(return_value=ctx.logger)
        return ctx

    async def test_execute_returns_results(self, mock_context: MagicMock) -> None:
        store = InMemoryKnowledgeStore()
        entry = KnowledgeEntry(
            content="Bracket uses 6061 aluminum",
            embedding=[1.0, 0.0, 0.0],
            knowledge_type=KnowledgeType.DESIGN_DECISION,
        )
        await store.store(entry)

        mock_emb = AsyncMock()
        mock_emb.embed.return_value = [1.0, 0.0, 0.0]

        handler = RetrieveKnowledgeHandler(mock_context, store, mock_emb)
        inp = RetrieveKnowledgeInput(query="aluminum bracket")
        output = await handler.execute(inp)

        assert output.total_results == 1
        assert output.results[0].content == "Bracket uses 6061 aluminum"

    async def test_execute_empty_results(self, mock_context: MagicMock) -> None:
        store = InMemoryKnowledgeStore()
        mock_emb = AsyncMock()
        mock_emb.embed.return_value = [1.0, 0.0, 0.0]

        handler = RetrieveKnowledgeHandler(mock_context, store, mock_emb)
        inp = RetrieveKnowledgeInput(query="nothing")
        output = await handler.execute(inp)
        assert output.total_results == 0


# ============================================================================
# IngestKnowledgeHandler tests
# ============================================================================


class TestIngestKnowledgeHandler:
    """Test the ingest_knowledge skill handler."""

    @pytest.fixture()
    def mock_context(self) -> MagicMock:
        ctx = MagicMock()
        ctx.logger = MagicMock()
        ctx.logger.bind = MagicMock(return_value=ctx.logger)
        return ctx

    async def test_execute_stores_entry(self, mock_context: MagicMock) -> None:
        store = InMemoryKnowledgeStore()
        mock_emb = AsyncMock()
        mock_emb.embed.return_value = [0.5, 0.5, 0.0]

        handler = IngestKnowledgeHandler(mock_context, store, mock_emb)
        inp = IngestKnowledgeInput(
            content="Use M3 screws",
            knowledge_type="design_decision",
            source="test",
        )
        output = await handler.execute(inp)

        assert output.embedded is True
        from uuid import UUID as _UUID

        stored = await store.get(_UUID(output.entry_id))
        assert stored is not None
        assert stored.content == "Use M3 screws"

    async def test_execute_no_embedding_service(self, mock_context: MagicMock) -> None:
        store = InMemoryKnowledgeStore()

        handler = IngestKnowledgeHandler(mock_context, store)
        inp = IngestKnowledgeInput(
            content="content",
            knowledge_type="session_summary",
            source="test",
        )
        output = await handler.execute(inp)
        assert output.embedded is False
        from uuid import UUID as _UUID

        assert await store.get(_UUID(output.entry_id)) is not None


# ============================================================================
# Knowledge API routes tests
# ============================================================================


class TestKnowledgeAPI:
    """Test the knowledge REST API endpoints."""

    @pytest.fixture()
    def client(self) -> TestClient:
        from api_gateway.knowledge.routes import router

        app = FastAPI()
        app.include_router(router)

        # Wire up in-memory store and mock embedding service
        store = InMemoryKnowledgeStore()
        mock_emb = AsyncMock()
        mock_emb.embed.return_value = [1.0, 0.0, 0.0]
        app.state.knowledge_store = store
        app.state.embedding_service = mock_emb

        return TestClient(app)

    def test_search_empty(self, client: TestClient) -> None:
        resp = client.get("/api/v1/knowledge/search", params={"query": "test"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["totalFound"] == 0
        assert data["results"] == []

    def test_ingest_and_search(self, client: TestClient) -> None:
        # Ingest
        resp = client.post(
            "/api/v1/knowledge/ingest",
            json={
                "content": "Use aluminum for the bracket",
                "knowledgeType": "design_decision",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["embedded"] is True
        entry_id = data["entryId"]

        # Search
        resp = client.get("/api/v1/knowledge/search", params={"query": "bracket material"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["totalFound"] == 1

        # Get by ID
        resp = client.get(f"/api/v1/knowledge/{entry_id}")
        assert resp.status_code == 200
        assert resp.json()["content"] == "Use aluminum for the bracket"

    def test_get_nonexistent(self, client: TestClient) -> None:
        resp = client.get(f"/api/v1/knowledge/{uuid4()}")
        assert resp.status_code == 404

    def test_ingest_with_metadata(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/knowledge/ingest",
            json={
                "content": "Component spec",
                "knowledgeType": "component",
                "metadata": {"datasheet": "DS-001"},
            },
        )
        assert resp.status_code == 201
        entry_id = resp.json()["entryId"]

        resp = client.get(f"/api/v1/knowledge/{entry_id}")
        assert resp.status_code == 200
        assert resp.json()["metadata"]["datasheet"] == "DS-001"

    def test_search_with_type_filter(self, client: TestClient) -> None:
        # Ingest two different types
        client.post(
            "/api/v1/knowledge/ingest",
            json={"content": "Decision A", "knowledgeType": "design_decision"},
        )
        client.post(
            "/api/v1/knowledge/ingest",
            json={"content": "Component B", "knowledgeType": "component"},
        )

        # Search with filter
        resp = client.get(
            "/api/v1/knowledge/search",
            params={"query": "test", "knowledgeType": "component"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["totalFound"] == 1
        assert data["results"][0]["knowledgeType"] == "component"

    def test_store_not_initialized(self) -> None:
        from api_gateway.knowledge.routes import router

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        resp = client.get("/api/v1/knowledge/search", params={"query": "test"})
        assert resp.status_code == 503
