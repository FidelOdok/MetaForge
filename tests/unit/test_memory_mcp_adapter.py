"""Unit tests for ``tool_registry.tools.memory.adapter``."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from digital_twin.knowledge.embedding_service import EmbeddingService
from digital_twin.memory.client import MemoryClient
from digital_twin.memory.models import ConfidenceTier, ExperienceMemory
from digital_twin.memory.store import InMemoryExperienceStore
from tool_registry.tools.memory.adapter import MemoryServer


class _FakeEmbeddings(EmbeddingService):
    DIM = 8

    async def embed(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return [(digest[i] - 128) / 128.0 for i in range(self.DIM)]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [await self.embed(t) for t in texts]


@pytest.fixture
async def wired_server() -> tuple[MemoryServer, InMemoryExperienceStore, _FakeEmbeddings]:
    store = InMemoryExperienceStore()
    embeddings = _FakeEmbeddings()
    server = MemoryServer(client=MemoryClient(store, embeddings))
    return server, store, embeddings


async def _seed(
    store: InMemoryExperienceStore,
    embeddings: _FakeEmbeddings,
    summaries: list[str],
    *,
    project_id: UUID | None = None,
    agent_code: str = "mechanical",
    success: bool = True,
) -> list[ExperienceMemory]:
    out: list[ExperienceMemory] = []
    for i, summary in enumerate(summaries):
        emb = await embeddings.embed(summary)
        exp = ExperienceMemory(
            id=uuid4(),
            run_id=f"run-{i}",
            step_id="s",
            agent_code=agent_code,
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
        await store.store(exp)
        out.append(exp)
    return out


def test_tool_manifest_registration():
    server = MemoryServer()
    tools = server._tools  # noqa: SLF001 — inspecting registration
    assert "memory.retrieve_similar_experience" in tools


def test_calling_without_client_bound_raises():
    server = MemoryServer()
    with pytest.raises(RuntimeError, match="set_client"):
        _ = server.client


@pytest.mark.asyncio
async def test_handler_returns_top_hit(wired_server):
    server, store, embeddings = wired_server
    await _seed(
        store,
        embeddings,
        ["task one description", "task two description", "task three description"],
    )

    payload = await server.handle_retrieve_similar_experience(
        {"goal": "task two description", "limit": 1}
    )
    assert "hits" in payload
    assert len(payload["hits"]) == 1
    top = payload["hits"][0]
    assert top["result_summary"] == "task two description"
    assert top["agent_code"] == "mechanical"
    assert top["rank"] == 0
    assert top["similarity"] == pytest.approx(1.0, abs=1e-6)


@pytest.mark.asyncio
async def test_handler_filters_by_agent_code(wired_server):
    server, store, embeddings = wired_server
    await _seed(store, embeddings, ["alpha"], agent_code="mech")
    await _seed(store, embeddings, ["alpha"], agent_code="elec")

    payload = await server.handle_retrieve_similar_experience(
        {"goal": "alpha", "agent_code": "elec", "limit": 5}
    )
    assert all(h["agent_code"] == "elec" for h in payload["hits"])


@pytest.mark.asyncio
async def test_handler_filters_by_only_success(wired_server):
    server, store, embeddings = wired_server
    await _seed(store, embeddings, ["ok run"], success=True)
    await _seed(store, embeddings, ["fail run"], success=False)

    only_success = await server.handle_retrieve_similar_experience(
        {"goal": "run", "only_success": True, "limit": 5}
    )
    assert all(h["success"] for h in only_success["hits"])

    only_failure = await server.handle_retrieve_similar_experience(
        {"goal": "run", "only_success": False, "limit": 5}
    )
    assert all(not h["success"] for h in only_failure["hits"])


@pytest.mark.asyncio
async def test_handler_rejects_missing_goal(wired_server):
    server, _store, _embeddings = wired_server
    with pytest.raises(ValueError, match="'goal' is required"):
        await server.handle_retrieve_similar_experience({})
    with pytest.raises(ValueError, match="'goal' is required"):
        await server.handle_retrieve_similar_experience({"goal": ""})


@pytest.mark.asyncio
async def test_handler_rejects_non_integer_limit(wired_server):
    server, _store, _embeddings = wired_server
    with pytest.raises(ValueError, match="'limit' must be an integer"):
        await server.handle_retrieve_similar_experience(
            {"goal": "alpha", "limit": "not-a-number"}
        )


@pytest.mark.asyncio
async def test_hit_payload_serializes_uuid_and_timestamp(wired_server):
    server, store, embeddings = wired_server
    project_id = UUID("00000000-0000-0000-0000-000000000123")
    await _seed(store, embeddings, ["alpha"], project_id=project_id)

    payload = await server.handle_retrieve_similar_experience({"goal": "alpha", "limit": 1})
    hit = payload["hits"][0]
    # All UUID / datetime fields must be strings, never raw objects
    assert isinstance(hit["experience_id"], str)
    assert isinstance(hit["project_id"], str)
    assert hit["project_id"] == str(project_id)
    assert isinstance(hit["timestamp"], str)
    assert hit["confidence"] == "verbatim"
