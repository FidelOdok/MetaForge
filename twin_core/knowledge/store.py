"""KnowledgeStore — semantic search and storage for cross-agent knowledge.

Provides in-memory vector search with pluggable embedding services.
Production deployments can back this with pgvector or similar.
"""

from __future__ import annotations

import math
from uuid import UUID

import structlog

from observability.tracing import get_tracer
from twin_core.knowledge.embedding_service import EmbeddingService, LocalHashEmbeddingService
from twin_core.knowledge.models import KnowledgeEntry, KnowledgeType, SearchResult

logger = structlog.get_logger(__name__)
tracer = get_tracer("knowledge.store")


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class KnowledgeStore:
    """In-memory knowledge store with semantic search.

    Stores KnowledgeEntry objects and supports:
    - Ingestion with automatic embedding
    - Semantic search by query string
    - Filtering by knowledge_type
    - Chunked ingestion for long documents
    """

    def __init__(
        self,
        embedding_service: EmbeddingService | None = None,
        chunk_size: int = 512,
        chunk_overlap: int = 64,
    ) -> None:
        self._embedding: EmbeddingService = embedding_service or LocalHashEmbeddingService()
        self._entries: dict[UUID, KnowledgeEntry] = {}
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap

    @property
    def count(self) -> int:
        """Number of entries in the store."""
        return len(self._entries)

    async def ingest(
        self,
        content: str,
        knowledge_type: KnowledgeType = KnowledgeType.GENERAL,
        source: str = "unknown",
        metadata: dict[str, str] | None = None,
    ) -> KnowledgeEntry:
        """Ingest a single piece of content, computing its embedding.

        Returns the created KnowledgeEntry (with embedding populated).
        """
        with tracer.start_as_current_span("knowledge.ingest") as span:
            span.set_attribute("knowledge.type", knowledge_type.value)
            span.set_attribute("knowledge.content_length", len(content))

            embedding = await self._embedding.embed(content)
            entry = KnowledgeEntry(
                content=content,
                knowledge_type=knowledge_type,
                source=source,
                metadata=metadata or {},
                embedding=embedding,
            )
            self._entries[entry.id] = entry

            logger.info(
                "Knowledge ingested",
                entry_id=str(entry.id),
                knowledge_type=knowledge_type.value,
                content_length=len(content),
            )
            return entry

    async def ingest_chunked(
        self,
        content: str,
        knowledge_type: KnowledgeType = KnowledgeType.GENERAL,
        source: str = "unknown",
        metadata: dict[str, str] | None = None,
    ) -> list[KnowledgeEntry]:
        """Ingest long content by splitting into overlapping chunks.

        Returns the list of created KnowledgeEntry objects.
        """
        with tracer.start_as_current_span("knowledge.ingest_chunked") as span:
            chunks = self._split_chunks(content)
            span.set_attribute("knowledge.chunk_count", len(chunks))
            span.set_attribute("knowledge.content_length", len(content))

            entries: list[KnowledgeEntry] = []
            chunk_meta = dict(metadata) if metadata else {}
            for idx, chunk in enumerate(chunks):
                chunk_meta_copy = dict(chunk_meta)
                chunk_meta_copy["chunk_index"] = str(idx)
                chunk_meta_copy["total_chunks"] = str(len(chunks))
                entry = await self.ingest(
                    content=chunk,
                    knowledge_type=knowledge_type,
                    source=source,
                    metadata=chunk_meta_copy,
                )
                entries.append(entry)

            logger.info(
                "Chunked knowledge ingested",
                chunk_count=len(entries),
                source=source,
            )
            return entries

    async def search(
        self,
        query: str,
        knowledge_type: KnowledgeType | None = None,
        limit: int = 5,
        min_score: float = 0.0,
    ) -> list[SearchResult]:
        """Semantic search over stored knowledge.

        Args:
            query: Natural language query.
            knowledge_type: Optional filter by type.
            limit: Maximum number of results.
            min_score: Minimum cosine similarity threshold.

        Returns:
            Ranked list of SearchResult (highest score first).
        """
        with tracer.start_as_current_span("knowledge.search") as span:
            span.set_attribute("knowledge.query_length", len(query))
            span.set_attribute("knowledge.limit", limit)
            if knowledge_type:
                span.set_attribute("knowledge.filter_type", knowledge_type.value)

            query_embedding = await self._embedding.embed(query)

            candidates: list[SearchResult] = []
            for entry in self._entries.values():
                if knowledge_type and entry.knowledge_type != knowledge_type:
                    continue
                if not entry.has_embedding:
                    continue
                score = _cosine_similarity(query_embedding, entry.embedding)
                if score >= min_score:
                    candidates.append(SearchResult(entry=entry, score=round(score, 6)))

            candidates.sort(key=lambda r: r.score, reverse=True)

            results = candidates[:limit]
            logger.info(
                "Knowledge search completed",
                query_length=len(query),
                results_count=len(results),
                total_candidates=len(candidates),
            )
            return results

    async def get(self, entry_id: UUID) -> KnowledgeEntry | None:
        """Retrieve a specific entry by ID."""
        return self._entries.get(entry_id)

    async def delete(self, entry_id: UUID) -> bool:
        """Delete an entry by ID. Returns True if found and deleted."""
        if entry_id in self._entries:
            del self._entries[entry_id]
            return True
        return False

    def _split_chunks(self, text: str) -> list[str]:
        """Split text into overlapping chunks."""
        if len(text) <= self._chunk_size:
            return [text]

        chunks: list[str] = []
        start = 0
        while start < len(text):
            end = start + self._chunk_size
            chunk = text[start:end]
            if chunk.strip():
                chunks.append(chunk)
            start += self._chunk_size - self._chunk_overlap

        return chunks if chunks else [text]
