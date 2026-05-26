"""Shared fixtures for the memory unit tests."""

from __future__ import annotations

import hashlib
from typing import Any

import pytest

from digital_twin.knowledge.embedding_service import EmbeddingService


class FakeEmbeddingService(EmbeddingService):
    """Deterministic 8-dimensional embedder for tests.

    Maps every input string to a stable unit-ish vector keyed by SHA-256
    of the text so identical text → identical embedding (the property
    real callers rely on) and different text → different embedding
    (the property similarity tests need).
    """

    DIM = 8

    async def embed(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return [
            (digest[i] - 128) / 128.0 for i in range(self.DIM)
        ]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [await self.embed(t) for t in texts]


@pytest.fixture
def fake_embeddings() -> FakeEmbeddingService:
    return FakeEmbeddingService()


@pytest.fixture
def fixed_uuid() -> Any:
    """A stable UUID for project_id filters."""
    from uuid import UUID

    return UUID("00000000-0000-0000-0000-000000000001")
