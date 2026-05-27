"""Integration test for ``POST /v1/memory/retrieve`` (MET-453).

Builds a minimal FastAPI app that mounts only the memory router and
wires ``app.state.memory_client`` to an in-memory experience store +
deterministic fake embedder — exercising the route end-to-end without
spinning up the full gateway.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_gateway.memory import router as memory_router
from digital_twin.knowledge.embedding_service import EmbeddingService
from digital_twin.memory.client import MemoryClient
from digital_twin.memory.models import ConfidenceTier, ExperienceMemory
from digital_twin.memory.store import InMemoryExperienceStore


class _FakeEmbeddings(EmbeddingService):
    DIM = 8

    async def embed(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return [(digest[i] - 128) / 128.0 for i in range(self.DIM)]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [await self.embed(t) for t in texts]


@pytest.fixture
def app_and_store() -> tuple[FastAPI, InMemoryExperienceStore, _FakeEmbeddings]:
    store = InMemoryExperienceStore()
    embeddings = _FakeEmbeddings()
    app = FastAPI()
    app.state.memory_store = store
    app.state.memory_client = MemoryClient(store, embeddings)
    app.include_router(memory_router)
    return app, store, embeddings


async def _seed(
    store: InMemoryExperienceStore,
    embeddings: _FakeEmbeddings,
    summaries: list[str],
    *,
    project_id: UUID | None = None,
    success: bool = True,
) -> None:
    for i, summary in enumerate(summaries):
        emb = await embeddings.embed(summary)
        await store.store(
            ExperienceMemory(
                id=uuid4(),
                run_id=f"run-{i}",
                step_id="s",
                agent_code="mechanical",
                task_type="validate",
                success=success,
                duration_seconds=1.0,
                result_summary=summary,
                error=None if success else "boom",
                project_id=project_id,
                timestamp=datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC),
                importance=0.7,
                confidence=ConfidenceTier.VERBATIM,
                embedding=emb,
            )
        )


def test_post_retrieve_returns_top_match(app_and_store):
    import anyio

    app, store, embeddings = app_and_store
    anyio.run(_seed, store, embeddings, ["alpha task", "beta task", "gamma task"])

    with TestClient(app) as client:
        response = client.post(
            "/v1/memory/retrieve",
            json={"goal": "beta task", "limit": 1},
        )
    assert response.status_code == 200
    body = response.json()
    # Response uses camelCase aliases (populate_by_name=True in schema).
    assert body["totalFound"] == 1
    assert body["query"] == "beta task"
    top = body["hits"][0]
    assert top["resultSummary"] == "beta task"
    assert top["rank"] == 0
    assert top["similarity"] == pytest.approx(1.0, abs=1e-6)


def test_post_retrieve_503_when_client_unbound():
    app = FastAPI()
    app.include_router(memory_router)
    with TestClient(app) as client:
        response = client.post("/v1/memory/retrieve", json={"goal": "anything"})
    assert response.status_code == 503
    assert response.json()["detail"] == "memory_client_not_ready"


def test_post_retrieve_rejects_empty_goal(app_and_store):
    app, _store, _embeddings = app_and_store
    with TestClient(app) as client:
        response = client.post("/v1/memory/retrieve", json={"goal": "", "limit": 5})
    assert response.status_code == 422


def test_post_retrieve_caps_limit_at_max(app_and_store):
    app, _store, _embeddings = app_and_store
    with TestClient(app) as client:
        response = client.post("/v1/memory/retrieve", json={"goal": "x", "limit": 9999})
    assert response.status_code == 422  # Pydantic rejects above MAX_RETRIEVAL_LIMIT


def test_post_retrieve_min_similarity_filters_weak_matches(app_and_store):
    import anyio

    app, store, embeddings = app_and_store
    anyio.run(_seed, store, embeddings, ["alpha task", "beta task", "gamma task"])

    with TestClient(app) as client:
        floored = client.post(
            "/v1/memory/retrieve",
            json={"goal": "alpha task", "limit": 5, "minSimilarity": 0.999},
        )
        unfiltered = client.post(
            "/v1/memory/retrieve",
            json={"goal": "alpha task", "limit": 5},
        )
    assert floored.status_code == 200
    assert unfiltered.status_code == 200
    # The floor keeps only the exact match; without it all three return.
    assert floored.json()["totalFound"] == 1
    assert floored.json()["hits"][0]["resultSummary"] == "alpha task"
    assert unfiltered.json()["totalFound"] == 3


def test_post_retrieve_rejects_out_of_range_min_similarity(app_and_store):
    app, _store, _embeddings = app_and_store
    with TestClient(app) as client:
        response = client.post(
            "/v1/memory/retrieve",
            json={"goal": "x", "minSimilarity": 2.0},
        )
    assert response.status_code == 422  # outside [-1.0, 1.0]
