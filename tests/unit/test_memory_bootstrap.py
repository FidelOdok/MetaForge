"""Unit tests for memory MCP adapter registration in ``bootstrap_tool_registry``."""

from __future__ import annotations

import hashlib
from typing import Any

import pytest

from digital_twin.knowledge.embedding_service import EmbeddingService
from digital_twin.memory.client import MemoryClient
from digital_twin.memory.store import InMemoryExperienceStore
from tool_registry.bootstrap import bootstrap_tool_registry


class _FakeEmbeddings(EmbeddingService):
    async def embed(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return [(digest[i] - 128) / 128.0 for i in range(8)]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [await self.embed(t) for t in texts]


@pytest.fixture
def memory_client() -> MemoryClient:
    return MemoryClient(InMemoryExperienceStore(), _FakeEmbeddings())


@pytest.mark.asyncio
async def test_bootstrap_registers_memory_adapter_when_client_supplied(memory_client):
    registry = await bootstrap_tool_registry(
        adapter_ids=[],  # skip the default adapter sweep — we only want memory
        memory_client=memory_client,
    )
    tools = registry.list_tools()
    tool_ids = {_tool_id(t) for t in tools}
    assert "memory.retrieve_similar_experience" in tool_ids


@pytest.mark.asyncio
async def test_bootstrap_registers_list_insights_when_insight_store_supplied(memory_client):
    from digital_twin.memory.consolidation.writer import InMemoryInsightStore

    registry = await bootstrap_tool_registry(
        adapter_ids=[],
        memory_client=memory_client,
        memory_insight_store=InMemoryInsightStore(),
    )
    tool_ids = {_tool_id(t) for t in registry.list_tools()}
    assert "memory.retrieve_similar_experience" in tool_ids
    assert "memory.list_insights" in tool_ids


@pytest.mark.asyncio
async def test_bootstrap_skips_memory_adapter_without_client():
    registry = await bootstrap_tool_registry(adapter_ids=[])
    tools = registry.list_tools()
    tool_ids = {_tool_id(t) for t in tools}
    assert "memory.retrieve_similar_experience" not in tool_ids


@pytest.mark.asyncio
async def test_bootstrap_respects_disabled_via_env(memory_client, monkeypatch):
    monkeypatch.setenv("METAFORGE_ADAPTER_MEMORY_ENABLED", "false")
    registry = await bootstrap_tool_registry(
        adapter_ids=[],
        memory_client=memory_client,
    )
    tools = registry.list_tools()
    tool_ids = {_tool_id(t) for t in tools}
    assert "memory.retrieve_similar_experience" not in tool_ids


def _tool_id(tool: Any) -> str:
    # ToolRegistry returns objects whose tool id lives on different
    # attributes depending on storage type — try the common ones.
    for attr in ("tool_id", "id", "name"):
        value = getattr(tool, attr, None)
        if value:
            return str(value)
    if isinstance(tool, dict):
        return str(tool.get("tool_id") or tool.get("id") or tool.get("name", ""))
    return str(tool)
