"""Embedding service for computing vector representations of text.

Provides an abstract interface and a local fallback implementation.
Production deployments can swap in OpenAI, Sentence-Transformers, etc.
"""

from __future__ import annotations

import hashlib
import math
from abc import ABC, abstractmethod

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("knowledge.embedding_service")

# Default embedding dimension for the local fallback
DEFAULT_DIMENSION = 128


class EmbeddingService(ABC):
    """Abstract interface for text embedding."""

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Dimensionality of produced embeddings."""
        ...

    @abstractmethod
    async def embed(self, text: str) -> list[float]:
        """Compute an embedding vector for the given text."""
        ...

    @abstractmethod
    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Compute embeddings for a batch of texts."""
        ...


class LocalHashEmbeddingService(EmbeddingService):
    """Deterministic hash-based embedding for development and testing.

    Not suitable for production semantic search — use a real model instead.
    Produces normalized vectors that are deterministic for the same input.
    """

    def __init__(self, dim: int = DEFAULT_DIMENSION) -> None:
        self._dim = dim

    @property
    def dimension(self) -> int:
        return self._dim

    async def embed(self, text: str) -> list[float]:
        with tracer.start_as_current_span("embedding.compute") as span:
            span.set_attribute("embedding.text_length", len(text))
            span.set_attribute("embedding.dimension", self._dim)
            return self._hash_embed(text)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        with tracer.start_as_current_span("embedding.compute_batch") as span:
            span.set_attribute("embedding.batch_size", len(texts))
            return [self._hash_embed(t) for t in texts]

    def _hash_embed(self, text: str) -> list[float]:
        """Create a deterministic pseudo-embedding from text via SHA-256 chaining."""
        raw: list[float] = []
        i = 0
        while len(raw) < self._dim:
            digest = hashlib.sha256(f"{text}:{i}".encode()).digest()
            for byte in digest:
                if len(raw) >= self._dim:
                    break
                raw.append((byte / 255.0) * 2 - 1)  # Map to [-1, 1]
            i += 1
        # L2 normalize
        norm = math.sqrt(sum(x * x for x in raw))
        if norm > 0:
            raw = [x / norm for x in raw]
        return raw
