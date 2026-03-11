"""Knowledge store implementations for semantic search over design knowledge.

Provides an abstract ``KnowledgeStore`` base class with two concrete
implementations:

- ``InMemoryKnowledgeStore`` — dict-backed, for development and testing.
- ``PgVectorKnowledgeStore`` — PostgreSQL + pgvector for production.
"""

from __future__ import annotations

import math
import time
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

import structlog
from pydantic import BaseModel, Field

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("digital_twin.knowledge.store")


# ---------------------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------------------


class KnowledgeType(StrEnum):
    """Categories of knowledge stored in the knowledge layer."""

    DESIGN_DECISION = "design_decision"
    COMPONENT = "component"
    FAILURE = "failure"
    CONSTRAINT = "constraint"
    SESSION = "session"


class KnowledgeEntry(BaseModel):
    """A single piece of indexed design knowledge."""

    id: UUID = Field(default_factory=uuid4, description="Unique entry ID")
    content: str = Field(..., min_length=1, description="Text content of the knowledge entry")
    embedding: list[float] = Field(default_factory=list, description="Vector embedding")
    knowledge_type: KnowledgeType = Field(..., description="Category of knowledge")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Arbitrary metadata")
    source_artifact_id: UUID | None = Field(
        default=None, description="ID of the source artifact in the Digital Twin"
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="When the entry was created",
    )


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class KnowledgeStore(ABC):
    """Abstract base class for knowledge storage backends."""

    @abstractmethod
    async def store(self, entry: KnowledgeEntry) -> KnowledgeEntry:
        """Persist a knowledge entry and return it with any generated fields."""
        ...

    @abstractmethod
    async def search(
        self,
        embedding: list[float],
        knowledge_type: KnowledgeType | None = None,
        limit: int = 5,
    ) -> list[KnowledgeEntry]:
        """Return the closest entries by cosine similarity."""
        ...

    @abstractmethod
    async def get(self, entry_id: UUID) -> KnowledgeEntry | None:
        """Retrieve a single entry by ID."""
        ...

    @abstractmethod
    async def delete(self, entry_id: UUID) -> bool:
        """Delete an entry.  Returns True if it existed."""
        ...

    @abstractmethod
    async def list(
        self,
        knowledge_type: KnowledgeType | None = None,
        limit: int = 50,
    ) -> list[KnowledgeEntry]:
        """List entries, optionally filtered by type."""
        ...


# ---------------------------------------------------------------------------
# In-memory implementation
# ---------------------------------------------------------------------------


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if len(a) != len(b) or len(a) == 0:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0.0 or mag_b == 0.0:
        return 0.0
    return dot / (mag_a * mag_b)


class InMemoryKnowledgeStore(KnowledgeStore):
    """Dict-backed knowledge store for development and testing."""

    def __init__(self) -> None:
        self._entries: dict[UUID, KnowledgeEntry] = {}

    async def store(self, entry: KnowledgeEntry) -> KnowledgeEntry:
        with tracer.start_as_current_span("knowledge_store.store") as span:
            span.set_attribute("knowledge.type", str(entry.knowledge_type))
            span.set_attribute("knowledge.entry_id", str(entry.id))
            self._entries[entry.id] = entry
            logger.info(
                "knowledge_entry_stored",
                entry_id=str(entry.id),
                knowledge_type=str(entry.knowledge_type),
            )
            return entry

    async def search(
        self,
        embedding: list[float],
        knowledge_type: KnowledgeType | None = None,
        limit: int = 5,
    ) -> list[KnowledgeEntry]:
        with tracer.start_as_current_span("knowledge_store.search") as span:
            span.set_attribute("pgvector.top_k", limit)
            span.set_attribute("pgvector.query_embedding_dim", len(embedding))
            t0 = time.monotonic()

            candidates = list(self._entries.values())
            if knowledge_type is not None:
                candidates = [e for e in candidates if e.knowledge_type == knowledge_type]

            scored = [
                (entry, _cosine_similarity(embedding, entry.embedding))
                for entry in candidates
                if entry.embedding
            ]
            scored.sort(key=lambda x: x[1], reverse=True)

            results = [entry for entry, _score in scored[:limit]]

            elapsed = time.monotonic() - t0
            span.set_attribute("knowledge.result_count", len(results))
            logger.info(
                "knowledge_search_completed",
                result_count=len(results),
                knowledge_type=str(knowledge_type) if knowledge_type else "all",
                duration_ms=round(elapsed * 1000, 2),
            )
            return results

    async def get(self, entry_id: UUID) -> KnowledgeEntry | None:
        with tracer.start_as_current_span("knowledge_store.get") as span:
            span.set_attribute("knowledge.entry_id", str(entry_id))
            return self._entries.get(entry_id)

    async def delete(self, entry_id: UUID) -> bool:
        with tracer.start_as_current_span("knowledge_store.delete") as span:
            span.set_attribute("knowledge.entry_id", str(entry_id))
            removed = self._entries.pop(entry_id, None)
            if removed is not None:
                logger.info("knowledge_entry_deleted", entry_id=str(entry_id))
                return True
            return False

    async def list(
        self,
        knowledge_type: KnowledgeType | None = None,
        limit: int = 50,
    ) -> list[KnowledgeEntry]:
        with tracer.start_as_current_span("knowledge_store.list") as span:
            entries = list(self._entries.values())
            if knowledge_type is not None:
                entries = [e for e in entries if e.knowledge_type == knowledge_type]
            entries.sort(key=lambda e: e.created_at, reverse=True)
            result = entries[:limit]
            span.set_attribute("knowledge.result_count", len(result))
            return result


# ---------------------------------------------------------------------------
# PgVector implementation
# ---------------------------------------------------------------------------


class PgVectorKnowledgeStore(KnowledgeStore):
    """PostgreSQL + pgvector backed knowledge store for production.

    Uses ``asyncpg`` for async connection pooling and pgvector's
    ``vector`` type for embedding storage with cosine similarity search.
    """

    def __init__(self, dsn: str, pool_size: int = 10) -> None:
        self._dsn = dsn
        self._pool_size = pool_size
        self._pool: Any = None

    async def initialize(self) -> None:
        """Create connection pool and ensure table + extension exist."""
        try:
            import asyncpg  # type: ignore[import-untyped]

            self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=self._pool_size)
            async with self._pool.acquire() as conn:
                await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS knowledge_entries (
                        id UUID PRIMARY KEY,
                        content TEXT NOT NULL,
                        embedding vector(384),
                        knowledge_type VARCHAR(50) NOT NULL,
                        metadata JSONB NOT NULL DEFAULT '{}',
                        source_artifact_id UUID,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                """)
            logger.info("pgvector_store_initialized", dsn=self._dsn)
        except Exception as exc:
            logger.error("pgvector_store_init_failed", error=str(exc))
            raise

    async def close(self) -> None:
        """Close the connection pool."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            logger.info("pgvector_store_closed")

    async def store(self, entry: KnowledgeEntry) -> KnowledgeEntry:
        with tracer.start_as_current_span("pgvector.store") as span:
            span.set_attribute("knowledge.type", str(entry.knowledge_type))
            span.set_attribute("knowledge.entry_id", str(entry.id))
            try:
                import json

                async with self._pool.acquire() as conn:
                    embedding_str = "[" + ",".join(str(v) for v in entry.embedding) + "]"
                    await conn.execute(
                        """
                        INSERT INTO knowledge_entries
                            (id, content, embedding, knowledge_type, metadata,
                             source_artifact_id, created_at)
                        VALUES ($1, $2, $3::vector, $4, $5::jsonb, $6, $7)
                        ON CONFLICT (id) DO UPDATE SET
                            content = EXCLUDED.content,
                            embedding = EXCLUDED.embedding,
                            knowledge_type = EXCLUDED.knowledge_type,
                            metadata = EXCLUDED.metadata,
                            source_artifact_id = EXCLUDED.source_artifact_id
                        """,
                        entry.id,
                        entry.content,
                        embedding_str,
                        str(entry.knowledge_type),
                        json.dumps(entry.metadata),
                        entry.source_artifact_id,
                        entry.created_at,
                    )
                logger.info(
                    "pgvector_entry_stored",
                    entry_id=str(entry.id),
                    knowledge_type=str(entry.knowledge_type),
                )
                return entry
            except Exception as exc:
                span.record_exception(exc)
                logger.error("pgvector_store_failed", error=str(exc))
                raise

    async def search(
        self,
        embedding: list[float],
        knowledge_type: KnowledgeType | None = None,
        limit: int = 5,
    ) -> list[KnowledgeEntry]:
        with tracer.start_as_current_span("pgvector.search") as span:
            span.set_attribute("pgvector.top_k", limit)
            span.set_attribute("pgvector.query_embedding_dim", len(embedding))
            t0 = time.monotonic()
            try:
                import json

                embedding_str = "[" + ",".join(str(v) for v in embedding) + "]"

                if knowledge_type is not None:
                    query = """
                        SELECT id, content, embedding::text, knowledge_type,
                               metadata, source_artifact_id, created_at
                        FROM knowledge_entries
                        WHERE knowledge_type = $1
                        ORDER BY embedding <=> $2::vector
                        LIMIT $3
                    """
                    params: tuple[Any, ...] = (str(knowledge_type), embedding_str, limit)
                else:
                    query = """
                        SELECT id, content, embedding::text, knowledge_type,
                               metadata, source_artifact_id, created_at
                        FROM knowledge_entries
                        ORDER BY embedding <=> $1::vector
                        LIMIT $2
                    """
                    params = (embedding_str, limit)

                async with self._pool.acquire() as conn:
                    rows = await conn.fetch(query, *params)

                results: list[KnowledgeEntry] = []
                for row in rows:
                    emb_text = row["embedding"]
                    emb_values = (
                        [float(v) for v in emb_text.strip("[]").split(",")] if emb_text else []
                    )
                    meta = row["metadata"]
                    if isinstance(meta, str):
                        meta = json.loads(meta)
                    results.append(
                        KnowledgeEntry(
                            id=row["id"],
                            content=row["content"],
                            embedding=emb_values,
                            knowledge_type=KnowledgeType(row["knowledge_type"]),
                            metadata=meta,
                            source_artifact_id=row["source_artifact_id"],
                            created_at=row["created_at"],
                        )
                    )

                elapsed = time.monotonic() - t0
                span.set_attribute("knowledge.result_count", len(results))
                logger.info(
                    "pgvector_search_completed",
                    result_count=len(results),
                    duration_ms=round(elapsed * 1000, 2),
                )
                return results
            except Exception as exc:
                span.record_exception(exc)
                logger.error("pgvector_search_failed", error=str(exc))
                raise

    async def get(self, entry_id: UUID) -> KnowledgeEntry | None:
        with tracer.start_as_current_span("pgvector.get") as span:
            span.set_attribute("knowledge.entry_id", str(entry_id))
            try:
                import json

                async with self._pool.acquire() as conn:
                    row = await conn.fetchrow(
                        """
                        SELECT id, content, embedding::text, knowledge_type,
                               metadata, source_artifact_id, created_at
                        FROM knowledge_entries
                        WHERE id = $1
                        """,
                        entry_id,
                    )
                if row is None:
                    return None
                emb_text = row["embedding"]
                emb_values = [float(v) for v in emb_text.strip("[]").split(",")] if emb_text else []
                meta = row["metadata"]
                if isinstance(meta, str):
                    meta = json.loads(meta)
                return KnowledgeEntry(
                    id=row["id"],
                    content=row["content"],
                    embedding=emb_values,
                    knowledge_type=KnowledgeType(row["knowledge_type"]),
                    metadata=meta,
                    source_artifact_id=row["source_artifact_id"],
                    created_at=row["created_at"],
                )
            except Exception as exc:
                span.record_exception(exc)
                logger.error("pgvector_get_failed", error=str(exc))
                raise

    async def delete(self, entry_id: UUID) -> bool:
        with tracer.start_as_current_span("pgvector.delete") as span:
            span.set_attribute("knowledge.entry_id", str(entry_id))
            try:
                async with self._pool.acquire() as conn:
                    result = await conn.execute(
                        "DELETE FROM knowledge_entries WHERE id = $1",
                        entry_id,
                    )
                deleted = result == "DELETE 1"
                if deleted:
                    logger.info("pgvector_entry_deleted", entry_id=str(entry_id))
                return deleted
            except Exception as exc:
                span.record_exception(exc)
                logger.error("pgvector_delete_failed", error=str(exc))
                raise

    async def list(
        self,
        knowledge_type: KnowledgeType | None = None,
        limit: int = 50,
    ) -> list[KnowledgeEntry]:
        with tracer.start_as_current_span("pgvector.list") as span:
            try:
                import json

                if knowledge_type is not None:
                    query = """
                        SELECT id, content, embedding::text, knowledge_type,
                               metadata, source_artifact_id, created_at
                        FROM knowledge_entries
                        WHERE knowledge_type = $1
                        ORDER BY created_at DESC
                        LIMIT $2
                    """
                    params: tuple[Any, ...] = (str(knowledge_type), limit)
                else:
                    query = """
                        SELECT id, content, embedding::text, knowledge_type,
                               metadata, source_artifact_id, created_at
                        FROM knowledge_entries
                        ORDER BY created_at DESC
                        LIMIT $1
                    """
                    params = (limit,)

                async with self._pool.acquire() as conn:
                    rows = await conn.fetch(query, *params)

                results: list[KnowledgeEntry] = []
                for row in rows:
                    emb_text = row["embedding"]
                    emb_values = (
                        [float(v) for v in emb_text.strip("[]").split(",")] if emb_text else []
                    )
                    meta = row["metadata"]
                    if isinstance(meta, str):
                        meta = json.loads(meta)
                    results.append(
                        KnowledgeEntry(
                            id=row["id"],
                            content=row["content"],
                            embedding=emb_values,
                            knowledge_type=KnowledgeType(row["knowledge_type"]),
                            metadata=meta,
                            source_artifact_id=row["source_artifact_id"],
                            created_at=row["created_at"],
                        )
                    )

                span.set_attribute("knowledge.result_count", len(results))
                return results
            except Exception as exc:
                span.record_exception(exc)
                logger.error("pgvector_list_failed", error=str(exc))
                raise
