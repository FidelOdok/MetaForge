"""Integration tests for the MET-471 knowledge-backed memory endpoints.

* ``POST /v1/memory/search`` — design-rationale search
* ``GET  /v1/memory/components/{name}`` — component-context lookup

Both wrap ``MemoryClient.search_design_rationale`` /
``MemoryClient.get_component_context``, which themselves wrap a
``KnowledgeService.search`` filtered by ``KnowledgeType``. This test
mounts only the memory router, wires a tiny ``_FakeKnowledgeService``
into ``MemoryClient`` via its optional ``knowledge_service`` kwarg, and
exercises both routes end-to-end without spinning up the full gateway.
"""

from __future__ import annotations

import hashlib
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_gateway.memory import router as memory_router
from digital_twin.knowledge.embedding_service import EmbeddingService
from digital_twin.knowledge.service import SearchHit
from digital_twin.knowledge.types import KnowledgeType
from digital_twin.memory.client import MemoryClient
from digital_twin.memory.store import InMemoryExperienceStore


class _FakeEmbeddings(EmbeddingService):
    DIM = 8

    async def embed(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return [(digest[i] - 128) / 128.0 for i in range(self.DIM)]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [await self.embed(t) for t in texts]


class _FakeKnowledgeService:
    """Minimal KnowledgeService stand-in that records calls + returns hits."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def search(
        self,
        query: str,
        top_k: int = 5,
        knowledge_type: KnowledgeType | None = None,
        filters: dict[str, Any] | None = None,
        project_id: UUID | None = None,
        rerank: bool = False,
        actor_id: str | None = None,
        include_historical: bool = False,
        hybrid: bool = False,
    ) -> list[SearchHit]:
        self.calls.append(
            {
                "query": query,
                "top_k": top_k,
                "knowledge_type": knowledge_type,
                "project_id": project_id,
            }
        )
        return [
            SearchHit(
                content=f"Hit for {query} (type={knowledge_type})",
                similarity_score=0.91,
                source_path="kb/design_decisions/2026-05.md",
                heading="Decision",
                chunk_index=0,
                total_chunks=1,
                metadata={"mpn": "BME280"},
                knowledge_type=knowledge_type,
                source_work_product_id=None,
            )
        ]


@pytest.fixture
def app_with_knowledge() -> tuple[FastAPI, _FakeKnowledgeService]:
    knowledge = _FakeKnowledgeService()
    embeddings = _FakeEmbeddings()
    store = InMemoryExperienceStore()
    app = FastAPI()
    # knowledge_service is typed as the ``KnowledgeService`` Protocol;
    # the fake is structurally compatible but mypy can't infer it from
    # a plain class. Cast at the call site.
    app.state.memory_client = MemoryClient(
        store=store,
        embeddings=embeddings,
        knowledge_service=knowledge,  # type: ignore[arg-type]
    )
    app.include_router(memory_router)
    return app, knowledge


# ---------------------------------------------------------------------------
# POST /v1/memory/search — design-rationale
# ---------------------------------------------------------------------------


def test_post_memory_search_returns_design_decision_hits(app_with_knowledge):
    app, knowledge = app_with_knowledge
    client = TestClient(app)

    response = client.post(
        "/v1/memory/search",
        json={"query": "why did we pick the BME280 over BMP280?", "limit": 3},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["query"].startswith("why did we pick")
    assert body["totalFound"] == 1
    hits = body["hits"]
    assert len(hits) == 1
    assert hits[0]["content"].startswith("Hit for")
    assert hits[0]["similarityScore"] == pytest.approx(0.91)
    assert hits[0]["sourcePath"] == "kb/design_decisions/2026-05.md"

    # MemoryClient.search_design_rationale forwards KnowledgeType.DESIGN_DECISION.
    assert knowledge.calls[-1]["knowledge_type"] is KnowledgeType.DESIGN_DECISION
    assert knowledge.calls[-1]["top_k"] == 3


def test_post_memory_search_rejects_empty_query(app_with_knowledge):
    app, _ = app_with_knowledge
    client = TestClient(app)

    response = client.post(
        "/v1/memory/search",
        json={"query": "", "limit": 5},
    )
    # Pydantic min_length=1 validation surfaces as 422.
    assert response.status_code == 422


def test_post_memory_search_503_when_knowledge_service_missing():
    """No knowledge_service wired → MemoryClient raises → 503."""
    embeddings = _FakeEmbeddings()
    store = InMemoryExperienceStore()
    app = FastAPI()
    app.state.memory_client = MemoryClient(store=store, embeddings=embeddings)
    app.include_router(memory_router)
    client = TestClient(app)

    response = client.post(
        "/v1/memory/search",
        json={"query": "anything", "limit": 1},
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "memory_client_knowledge_service_not_ready"


# ---------------------------------------------------------------------------
# GET /v1/memory/components/{name} — component context
# ---------------------------------------------------------------------------


def test_get_component_context_returns_component_hits(app_with_knowledge):
    app, knowledge = app_with_knowledge
    client = TestClient(app)

    response = client.get("/v1/memory/components/BME280?limit=2")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["query"] == "BME280"
    assert body["totalFound"] == 1
    hits = body["hits"]
    assert hits[0]["content"].startswith("Hit for")

    # MemoryClient.get_component_context forwards KnowledgeType.COMPONENT
    # and uses the URL path segment as the search query.
    assert knowledge.calls[-1]["knowledge_type"] is KnowledgeType.COMPONENT
    assert knowledge.calls[-1]["query"] == "BME280"
    assert knowledge.calls[-1]["top_k"] == 2


def test_get_component_context_rejects_whitespace_only_name(app_with_knowledge):
    app, _ = app_with_knowledge
    client = TestClient(app)

    # FastAPI URL-decodes "%20" → " "; the route guards against
    # blank names with a 422.
    response = client.get("/v1/memory/components/%20%20")
    assert response.status_code == 422
    assert "non-empty" in response.json()["detail"]


def test_get_component_context_503_when_knowledge_service_missing():
    embeddings = _FakeEmbeddings()
    store = InMemoryExperienceStore()
    app = FastAPI()
    app.state.memory_client = MemoryClient(store=store, embeddings=embeddings)
    app.include_router(memory_router)
    client = TestClient(app)

    response = client.get("/v1/memory/components/BME280")
    assert response.status_code == 503
    assert response.json()["detail"] == "memory_client_knowledge_service_not_ready"


def test_get_component_context_503_when_memory_client_missing():
    """No memory_client on app.state → 503 from ``_get_client``."""
    app = FastAPI()
    app.include_router(memory_router)
    client = TestClient(app)

    response = client.get("/v1/memory/components/BME280")
    assert response.status_code == 503
    assert response.json()["detail"] == "memory_client_not_ready"
