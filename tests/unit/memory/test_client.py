"""Unit tests for ``digital_twin.memory.client``."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from digital_twin.memory.client import MAX_RETRIEVAL_LIMIT, MemoryClient
from digital_twin.memory.models import ConfidenceTier, ExperienceMemory
from digital_twin.memory.store import InMemoryExperienceStore


async def _seed(store: InMemoryExperienceStore, embedder, *, count: int = 3) -> None:
    for i in range(count):
        text = f"task description {i}"
        embedding = await embedder.embed(text)
        await store.store(
            ExperienceMemory(
                id=uuid4(),
                run_id=f"run-{i}",
                step_id="s",
                agent_code="mechanical",
                task_type="validate",
                success=True,
                result_summary=text,
                timestamp=datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC),
                importance=0.6,
                confidence=ConfidenceTier.VERBATIM,
                embedding=embedding,
            )
        )


@pytest.mark.asyncio
async def test_retrieve_returns_nearest_neighbour(fake_embeddings):
    store = InMemoryExperienceStore()
    await _seed(store, fake_embeddings)
    client = MemoryClient(store, fake_embeddings)

    hits = await client.retrieve_similar_experience("task description 1", limit=1)
    assert len(hits) == 1
    assert hits[0].experience.result_summary == "task description 1"
    assert hits[0].similarity == pytest.approx(1.0, abs=1e-6)


@pytest.mark.asyncio
async def test_retrieve_empty_goal_returns_empty_list(fake_embeddings):
    store = InMemoryExperienceStore()
    await _seed(store, fake_embeddings)
    client = MemoryClient(store, fake_embeddings)

    assert await client.retrieve_similar_experience("") == []
    assert await client.retrieve_similar_experience("   ") == []


@pytest.mark.asyncio
async def test_retrieve_caps_limit(fake_embeddings):
    store = InMemoryExperienceStore()
    await _seed(store, fake_embeddings, count=10)
    client = MemoryClient(store, fake_embeddings)

    hits = await client.retrieve_similar_experience(
        "task description 0",
        limit=MAX_RETRIEVAL_LIMIT + 100,
    )
    assert len(hits) <= MAX_RETRIEVAL_LIMIT


@pytest.mark.asyncio
async def test_retrieve_strips_goal_before_embedding(fake_embeddings):
    store = InMemoryExperienceStore()
    await _seed(store, fake_embeddings)
    client = MemoryClient(store, fake_embeddings)

    padded = await client.retrieve_similar_experience("  task description 1  ", limit=1)
    trimmed = await client.retrieve_similar_experience("task description 1", limit=1)
    assert padded[0].experience.id == trimmed[0].experience.id


@pytest.mark.asyncio
async def test_retrieve_results_ranked_by_similarity(fake_embeddings):
    store = InMemoryExperienceStore()
    await _seed(store, fake_embeddings, count=5)
    client = MemoryClient(store, fake_embeddings)

    hits = await client.retrieve_similar_experience("task description 2", limit=5)

    # Descending similarity, and rank reflects that order.
    sims = [h.similarity for h in hits]
    assert sims == sorted(sims, reverse=True)
    assert [h.rank for h in hits] == list(range(len(hits)))
    # The exact-match experience is the top hit.
    assert hits[0].experience.result_summary == "task description 2"


@pytest.mark.asyncio
async def test_min_similarity_floor_drops_weak_matches(fake_embeddings):
    store = InMemoryExperienceStore()
    await _seed(store, fake_embeddings, count=5)
    client = MemoryClient(store, fake_embeddings)

    # A very high floor keeps only the exact (similarity ~1.0) match.
    strict = await client.retrieve_similar_experience(
        "task description 3", limit=5, min_similarity=0.999
    )
    assert len(strict) == 1
    assert strict[0].experience.result_summary == "task description 3"
    assert strict[0].similarity >= 0.999


@pytest.mark.asyncio
async def test_min_similarity_none_keeps_all_hits(fake_embeddings):
    store = InMemoryExperienceStore()
    await _seed(store, fake_embeddings, count=5)
    client = MemoryClient(store, fake_embeddings)

    unfiltered = await client.retrieve_similar_experience("task description 3", limit=5)
    floored = await client.retrieve_similar_experience(
        "task description 3", limit=5, min_similarity=None
    )
    assert len(unfiltered) == len(floored) == 5


@pytest.mark.asyncio
async def test_min_similarity_all_filtered_returns_empty(fake_embeddings):
    store = InMemoryExperienceStore()
    await _seed(store, fake_embeddings, count=3)
    client = MemoryClient(store, fake_embeddings)

    # Floor above the max possible cosine similarity → nothing qualifies.
    hits = await client.retrieve_similar_experience(
        "task description 1", limit=3, min_similarity=1.0001
    )
    assert hits == []
