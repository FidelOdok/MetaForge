"""Unit tests for ``metaforge.mcp.__main__._build_memory_client`` (MET-453)."""

from __future__ import annotations

import pytest

from digital_twin.memory.client import MemoryClient
from digital_twin.memory.store import InMemoryExperienceStore
from metaforge.mcp.__main__ import _build_memory_client, _close_memory_store


@pytest.mark.asyncio
async def test_build_memory_client_returns_in_memory_when_no_db_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    client, store = await _build_memory_client()
    try:
        assert isinstance(client, MemoryClient)
        assert isinstance(store, InMemoryExperienceStore)
    finally:
        await _close_memory_store(store)


@pytest.mark.asyncio
async def test_build_memory_client_falls_back_to_in_memory_on_pgvector_failure(monkeypatch):
    # Point at an unreachable DSN so PgVectorExperienceStore.initialize raises;
    # the wiring should swallow and substitute the in-memory backend.
    monkeypatch.setenv("DATABASE_URL", "postgresql://invalid:invalid@127.0.0.1:1/none")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    client, store = await _build_memory_client()
    try:
        assert isinstance(client, MemoryClient)
        assert isinstance(store, InMemoryExperienceStore)
    finally:
        await _close_memory_store(store)


@pytest.mark.asyncio
async def test_close_memory_store_tolerates_none():
    # Defensive: the close helper must not raise on a None argument because
    # _build_memory_client may return (None, None) on cold-path failures.
    await _close_memory_store(None)


@pytest.mark.asyncio
async def test_close_memory_store_tolerates_no_close_attr():
    # InMemoryExperienceStore has no ``close`` method — the helper should
    # silently no-op rather than AttributeError.
    store = InMemoryExperienceStore()
    await _close_memory_store(store)
