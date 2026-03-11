"""Embedding service abstractions for the Knowledge Layer.

Provides text-to-vector embedding using local models (sentence-transformers)
or remote APIs (OpenAI).  A factory function selects the appropriate provider.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Any

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("digital_twin.knowledge.embedding_service")

# Dimensionality for the default local model (all-MiniLM-L6-v2)
LOCAL_EMBEDDING_DIM = 384


class EmbeddingService(ABC):
    """Abstract base class for text embedding providers."""

    @abstractmethod
    async def embed(self, text: str) -> list[float]:
        """Embed a single text string into a vector."""
        ...

    @abstractmethod
    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts into vectors."""
        ...


class LocalEmbeddingService(EmbeddingService):
    """Embedding service using sentence-transformers (all-MiniLM-L6-v2).

    The model is loaded lazily on first use.  If ``sentence-transformers``
    is not installed, a zero-vector fallback is returned with a warning.
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        self._model_name = model_name
        self._model: Any = None
        self._available: bool | None = None

    def _load_model(self) -> None:
        """Lazy-load the sentence-transformers model."""
        if self._available is not None:
            return
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore[import-untyped]

            self._model = SentenceTransformer(self._model_name)
            self._available = True
            logger.info(
                "embedding_model_loaded",
                model=self._model_name,
            )
        except ImportError:
            self._available = False
            logger.warning(
                "sentence_transformers_not_installed",
                fallback="zero_vector",
            )

    async def embed(self, text: str) -> list[float]:
        with tracer.start_as_current_span("embedding.local.embed") as span:
            span.set_attribute("embedding.model", self._model_name)
            span.set_attribute("embedding.text_length", len(text))
            self._load_model()
            if not self._available or self._model is None:
                return [0.0] * LOCAL_EMBEDDING_DIM
            result = self._model.encode(text, convert_to_numpy=True)
            return result.tolist()  # type: ignore[no-any-return]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        with tracer.start_as_current_span("embedding.local.embed_batch") as span:
            span.set_attribute("embedding.model", self._model_name)
            span.set_attribute("embedding.batch_size", len(texts))
            self._load_model()
            if not self._available or self._model is None:
                return [[0.0] * LOCAL_EMBEDDING_DIM for _ in texts]
            results = self._model.encode(texts, convert_to_numpy=True)
            return [r.tolist() for r in results]  # type: ignore[union-attr]


class OpenAIEmbeddingService(EmbeddingService):
    """Embedding service using the OpenAI text-embedding-3-small model.

    Reads ``OPENAI_API_KEY`` from the environment.
    """

    def __init__(
        self,
        model: str = "text-embedding-3-small",
        api_key: str | None = None,
    ) -> None:
        self._model = model
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            from openai import AsyncOpenAI  # type: ignore[import-untyped]

            self._client = AsyncOpenAI(api_key=self._api_key)
        return self._client

    async def embed(self, text: str) -> list[float]:
        with tracer.start_as_current_span("embedding.openai.embed") as span:
            span.set_attribute("embedding.model", self._model)
            span.set_attribute("embedding.text_length", len(text))
            try:
                client = self._get_client()
                response = await client.embeddings.create(model=self._model, input=text)
                return response.data[0].embedding  # type: ignore[no-any-return]
            except Exception as exc:
                span.record_exception(exc)
                logger.error("openai_embedding_failed", error=str(exc))
                raise

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        with tracer.start_as_current_span("embedding.openai.embed_batch") as span:
            span.set_attribute("embedding.model", self._model)
            span.set_attribute("embedding.batch_size", len(texts))
            try:
                client = self._get_client()
                response = await client.embeddings.create(model=self._model, input=texts)
                return [d.embedding for d in response.data]  # type: ignore[no-any-return]
            except Exception as exc:
                span.record_exception(exc)
                logger.error("openai_embedding_batch_failed", error=str(exc))
                raise


def create_embedding_service(
    provider: str = "local",
    **kwargs: Any,
) -> EmbeddingService:
    """Factory to create an embedding service by provider name.

    Parameters
    ----------
    provider:
        ``"local"`` for sentence-transformers, ``"openai"`` for OpenAI API.
    **kwargs:
        Extra keyword args forwarded to the constructor.
    """
    if provider == "local":
        return LocalEmbeddingService(**kwargs)
    if provider == "openai":
        return OpenAIEmbeddingService(**kwargs)
    raise ValueError(f"Unknown embedding provider: {provider!r}. Use 'local' or 'openai'.")
