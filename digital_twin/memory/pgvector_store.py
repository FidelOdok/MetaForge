"""PostgreSQL + pgvector backend for the experience store.

Mirrors ``digital_twin.knowledge.store.PgVectorKnowledgeStore`` but
stores ``ExperienceMemory`` rows. The schema is independent — agent
experiences and design knowledge are different shapes — so this lives
in its own table (``agent_experiences``) and never touches
``knowledge_entries``.

Embedding dimension defaults to 384 (``LocalEmbeddingService``); the
gateway is expected to pin it to whichever embedder is wired on
``app.state.embedding_service``. Mixing dimensions in one table is
unsupported by pgvector, so the gateway must initialize this store
with the same dim used at index time.
"""

from __future__ import annotations

import json
import math
import time
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog

from digital_twin.memory.models import ConfidenceTier, ExperienceMemory, MemorySearchHit
from digital_twin.memory.store import ExperienceStore
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("digital_twin.memory.pgvector_store")

DEFAULT_EMBEDDING_DIM = 384

# ivfflat list count for the embedding ANN index. 100 is pgvector's
# common default for small-to-mid corpora; retune (≈ rows/1000) once the
# table is large. Cosine ops match the ``<=>`` operator used in ``search``.
DEFAULT_IVFFLAT_LISTS = 100


def schema_statements(
    embedding_dim: int,
    *,
    ivfflat_lists: int = DEFAULT_IVFFLAT_LISTS,
) -> list[str]:
    """DDL that provisions the ``agent_experiences`` pgvector schema (MET-457).

    Returned as an ordered list so ``initialize`` can run them in
    sequence and unit tests can assert the schema shape (table,
    dimension, JSONB metadata, and — critically — the ivfflat cosine
    index on the embedding column) without a live database.
    """
    return [
        "CREATE EXTENSION IF NOT EXISTS vector",
        f"""
        CREATE TABLE IF NOT EXISTS agent_experiences (
            id UUID PRIMARY KEY,
            run_id TEXT NOT NULL,
            step_id TEXT NOT NULL,
            agent_code TEXT NOT NULL,
            task_type TEXT NOT NULL DEFAULT '',
            success BOOLEAN NOT NULL,
            duration_seconds DOUBLE PRECISION,
            result_summary TEXT NOT NULL DEFAULT '',
            error TEXT,
            project_id UUID,
            timestamp TIMESTAMPTZ NOT NULL,
            importance DOUBLE PRECISION NOT NULL,
            confidence TEXT NOT NULL,
            embedding vector({embedding_dim}),
            metadata JSONB NOT NULL DEFAULT '{{}}'
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_agent_experiences_run ON agent_experiences (run_id)",
        "CREATE INDEX IF NOT EXISTS idx_agent_experiences_project "
        "ON agent_experiences (project_id)",
        # MET-457: ivfflat ANN index on the embedding column so cosine
        # search scales past a sequential scan as the corpus grows.
        "CREATE INDEX IF NOT EXISTS idx_agent_experiences_embedding "
        "ON agent_experiences USING ivfflat (embedding vector_cosine_ops) "
        f"WITH (lists = {ivfflat_lists})",
    ]


class PgVectorExperienceStore(ExperienceStore):
    """PostgreSQL + pgvector backend for ``ExperienceMemory`` records.

    Use ``await store.initialize()`` once at gateway boot to create the
    pool and the ``agent_experiences`` table. Pair with ``close()`` on
    shutdown.
    """

    def __init__(
        self,
        dsn: str,
        *,
        embedding_dim: int = DEFAULT_EMBEDDING_DIM,
        pool_size: int = 10,
    ) -> None:
        self._dsn = dsn
        self._embedding_dim = embedding_dim
        self._pool_size = pool_size
        self._pool: Any = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Create connection pool + ensure extension and table exist."""
        try:
            import asyncpg

            self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=self._pool_size)
            async with self._pool.acquire() as conn:
                for statement in schema_statements(self._embedding_dim):
                    await conn.execute(statement)
            logger.info(
                "pgvector_experience_store_initialized",
                embedding_dim=self._embedding_dim,
            )
        except Exception as exc:
            logger.error("pgvector_experience_store_init_failed", error=str(exc))
            raise

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            logger.info("pgvector_experience_store_closed")

    # ------------------------------------------------------------------
    # ExperienceStore impl
    # ------------------------------------------------------------------

    async def store(self, experience: ExperienceMemory) -> ExperienceMemory:
        with tracer.start_as_current_span("pgvector_experience.store") as span:
            span.set_attribute("experience.id", str(experience.id))
            span.set_attribute("experience.agent_code", experience.agent_code)
            try:
                embedding_str = _vector_literal(experience.embedding)
                async with self._pool.acquire() as conn:
                    await conn.execute(
                        """
                        INSERT INTO agent_experiences
                            (id, run_id, step_id, agent_code, task_type, success,
                             duration_seconds, result_summary, error, project_id,
                             timestamp, importance, confidence, embedding, metadata)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                                $11, $12, $13, $14::vector, $15::jsonb)
                        ON CONFLICT (id) DO UPDATE SET
                            run_id = EXCLUDED.run_id,
                            step_id = EXCLUDED.step_id,
                            agent_code = EXCLUDED.agent_code,
                            task_type = EXCLUDED.task_type,
                            success = EXCLUDED.success,
                            duration_seconds = EXCLUDED.duration_seconds,
                            result_summary = EXCLUDED.result_summary,
                            error = EXCLUDED.error,
                            project_id = EXCLUDED.project_id,
                            timestamp = EXCLUDED.timestamp,
                            importance = EXCLUDED.importance,
                            confidence = EXCLUDED.confidence,
                            embedding = EXCLUDED.embedding,
                            metadata = EXCLUDED.metadata
                        """,
                        experience.id,
                        experience.run_id,
                        experience.step_id,
                        experience.agent_code,
                        experience.task_type,
                        experience.success,
                        experience.duration_seconds,
                        experience.result_summary,
                        experience.error,
                        experience.project_id,
                        experience.timestamp,
                        experience.importance,
                        str(experience.confidence),
                        embedding_str,
                        json.dumps(experience.metadata),
                    )
                logger.info(
                    "pgvector_experience_stored",
                    experience_id=str(experience.id),
                    agent_code=experience.agent_code,
                    success=experience.success,
                )
                return experience
            except Exception as exc:
                span.record_exception(exc)
                logger.error("pgvector_experience_store_failed", error=str(exc))
                raise

    async def search(
        self,
        embedding: list[float],
        *,
        limit: int = 5,
        project_id: UUID | None = None,
        agent_code: str | None = None,
        only_success: bool | None = None,
    ) -> list[MemorySearchHit]:
        with tracer.start_as_current_span("pgvector_experience.search") as span:
            span.set_attribute("memory.top_k", limit)
            span.set_attribute("memory.query_embedding_dim", len(embedding))
            t0 = time.monotonic()
            try:
                embedding_str = _vector_literal(embedding)
                clauses: list[str] = []
                params: list[Any] = [embedding_str]
                if project_id is not None:
                    params.append(project_id)
                    clauses.append(f"project_id = ${len(params)}")
                if agent_code is not None:
                    params.append(agent_code)
                    clauses.append(f"agent_code = ${len(params)}")
                if only_success is not None:
                    params.append(only_success)
                    clauses.append(f"success = ${len(params)}")
                params.append(limit)

                where_clause = (" WHERE " + " AND ".join(clauses)) if clauses else ""
                query = f"""
                    SELECT id, run_id, step_id, agent_code, task_type, success,
                           duration_seconds, result_summary, error, project_id,
                           timestamp, importance, confidence,
                           embedding::text, metadata,
                           1 - (embedding <=> $1::vector) AS similarity
                    FROM agent_experiences
                    {where_clause}
                    ORDER BY embedding <=> $1::vector
                    LIMIT ${len(params)}
                """
                async with self._pool.acquire() as conn:
                    # MET-454-fu: bump ivfflat.probes inside the search
                    # transaction so low-volume tables (early in the
                    # lifecycle, before consolidation kicks in) still
                    # return hits. With ``lists = sqrt(N)`` and the
                    # default ``probes = 1``, an ivfflat scan over 3-20
                    # rows lands in mostly empty centroids and returns
                    # nothing. Raising probes to 10 widens the search to
                    # every centroid we have in practice — recall climbs
                    # at a tiny scan-cost we do not care about at this
                    # volume. Once the table grows past a few thousand
                    # rows we can revisit and tune lists / probes
                    # together (or move to HNSW like the LightRAG
                    # tables). ``SET LOCAL`` is transaction-scoped, so
                    # the override does not leak across pool checkouts.
                    async with conn.transaction():
                        await conn.execute("SET LOCAL ivfflat.probes = 10")
                        rows = await conn.fetch(query, *params)

                hits: list[MemorySearchHit] = []
                for rank, row in enumerate(rows):
                    experience = _row_to_experience(row)
                    # Clamp similarity to [-1, 1] — pgvector returns
                    # `1 - cosine_distance`, which can drift outside the
                    # valid range by a few ULPs on identical vectors.
                    similarity = max(-1.0, min(1.0, float(row["similarity"])))
                    hits.append(
                        MemorySearchHit(
                            experience=experience,
                            similarity=similarity,
                            rank=rank,
                        )
                    )
                elapsed_ms = (time.monotonic() - t0) * 1000.0
                span.set_attribute("memory.result_count", len(hits))
                logger.info(
                    "pgvector_experience_search_completed",
                    result_count=len(hits),
                    project_id=str(project_id) if project_id else None,
                    agent_code=agent_code,
                    duration_ms=round(elapsed_ms, 2),
                )
                return hits
            except Exception as exc:
                span.record_exception(exc)
                logger.error("pgvector_experience_search_failed", error=str(exc))
                raise

    async def get(self, experience_id: UUID) -> ExperienceMemory | None:
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT id, run_id, step_id, agent_code, task_type, success,
                           duration_seconds, result_summary, error, project_id,
                           timestamp, importance, confidence,
                           embedding::text, metadata
                    FROM agent_experiences
                    WHERE id = $1
                    """,
                    experience_id,
                )
            if row is None:
                return None
            return _row_to_experience(row)
        except Exception as exc:
            logger.error("pgvector_experience_get_failed", error=str(exc))
            raise

    async def delete(self, experience_id: UUID) -> bool:
        try:
            async with self._pool.acquire() as conn:
                result = await conn.execute(
                    "DELETE FROM agent_experiences WHERE id = $1",
                    experience_id,
                )
            deleted = bool(result == "DELETE 1")
            if deleted:
                logger.info("pgvector_experience_deleted", experience_id=str(experience_id))
            return deleted
        except Exception as exc:
            logger.error("pgvector_experience_delete_failed", error=str(exc))
            raise

    async def delete_by_run(self, run_id: str) -> int:
        with tracer.start_as_current_span("pgvector_experience.delete_by_run") as span:
            span.set_attribute("memory.run_id", run_id)
            try:
                async with self._pool.acquire() as conn:
                    result = await conn.execute(
                        "DELETE FROM agent_experiences WHERE run_id = $1",
                        run_id,
                    )
                # asyncpg returns `DELETE <n>` on success; parse the count.
                deleted = _parse_delete_count(result)
                if deleted:
                    logger.info(
                        "pgvector_experience_run_deleted",
                        run_id=run_id,
                        deleted=deleted,
                    )
                return deleted
            except Exception as exc:
                span.record_exception(exc)
                logger.error("pgvector_experience_delete_by_run_failed", error=str(exc))
                raise


def _vector_literal(embedding: list[float]) -> str:
    """Serialize a list of floats to a pgvector input literal."""
    # NaN / inf would make pgvector reject the row; coerce to 0.0 so the
    # store never fails on a degenerate embedding from a broken model.
    safe = [v if math.isfinite(v) else 0.0 for v in embedding]
    return "[" + ",".join(repr(float(v)) for v in safe) + "]"


def _parse_delete_count(status: str) -> int:
    """asyncpg returns ``'DELETE <n>'`` for DELETE commands."""
    parts = status.split()
    if len(parts) == 2 and parts[0] == "DELETE":
        try:
            return int(parts[1])
        except ValueError:
            return 0
    return 0


def _row_to_experience(row: Any) -> ExperienceMemory:
    """Reconstruct an ``ExperienceMemory`` from an asyncpg row."""
    emb_text = row["embedding"]
    embedding = [float(v) for v in emb_text.strip("[]").split(",") if v] if emb_text else []
    metadata = row["metadata"]
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    ts = row["timestamp"]
    if ts is not None and ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ExperienceMemory(
        id=row["id"],
        run_id=row["run_id"],
        step_id=row["step_id"],
        agent_code=row["agent_code"],
        task_type=row["task_type"] or "",
        success=row["success"],
        duration_seconds=row["duration_seconds"],
        result_summary=row["result_summary"] or "",
        error=row["error"],
        project_id=row["project_id"],
        timestamp=ts if ts is not None else datetime.now(UTC),
        importance=row["importance"],
        confidence=ConfidenceTier(row["confidence"]),
        embedding=embedding,
        metadata=metadata or {},
    )
