"""PostgreSQL + pgvector backend for ``InsightStore``.

Mirrors ``digital_twin.memory.pgvector_store.PgVectorExperienceStore``
but stores synthesized consolidation insights (one row per insight,
not per supporting experience). The narrative is embedded for
semantic search; the supporting experience IDs are denormalised into
a ``uuid[]`` column so consumers can audit which events motivated each
insight without a join.

Embedding dimension defaults to 384 (LocalEmbeddingService) — pin via
the constructor when the gateway wires this against OpenAI's 1536-dim
text-embedding-3-small.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog

from digital_twin.memory.consolidation.insight import Insight, InsightKind
from digital_twin.memory.consolidation.themes import ConsolidationTheme
from digital_twin.memory.consolidation.writer import InsightStore
from digital_twin.memory.models import ConfidenceTier
from digital_twin.memory.pgvector_store import _vector_literal
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("digital_twin.memory.consolidation.pgvector_insight_store")

DEFAULT_EMBEDDING_DIM = 384


class PgVectorInsightStore(InsightStore):
    """PostgreSQL + pgvector backed ``InsightStore``.

    The narrative is stored both as raw text and as an embedding so the
    table can answer both ``list_by_theme`` queries and future
    nearest-neighbour searches. The embedding column is nullable so
    callers can persist insights before the embedder is wired without
    breaking writes.
    """

    def __init__(
        self,
        dsn: str,
        *,
        embedding_dim: int = DEFAULT_EMBEDDING_DIM,
        pool_size: int = 5,
    ) -> None:
        self._dsn = dsn
        self._embedding_dim = embedding_dim
        self._pool_size = pool_size
        self._pool: Any = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        try:
            import asyncpg

            self._pool = await asyncpg.create_pool(
                self._dsn, min_size=1, max_size=self._pool_size
            )
            async with self._pool.acquire() as conn:
                await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
                await conn.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS consolidation_insights (
                        id UUID PRIMARY KEY,
                        theme TEXT NOT NULL,
                        kind TEXT NOT NULL,
                        narrative TEXT NOT NULL,
                        supporting_experience_ids UUID[] NOT NULL DEFAULT '{{}}',
                        confidence DOUBLE PRECISION NOT NULL,
                        confidence_tier TEXT NOT NULL,
                        synthesized_at TIMESTAMPTZ NOT NULL,
                        narrative_embedding vector({self._embedding_dim})
                    )
                    """
                )
                await conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_consolidation_insights_theme "
                    "ON consolidation_insights (theme)"
                )
                await conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_consolidation_insights_synthesized_at "
                    "ON consolidation_insights (synthesized_at DESC)"
                )
            logger.info(
                "pgvector_insight_store_initialized",
                embedding_dim=self._embedding_dim,
            )
        except Exception as exc:
            logger.error("pgvector_insight_store_init_failed", error=str(exc))
            raise

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            logger.info("pgvector_insight_store_closed")

    # ------------------------------------------------------------------
    # InsightStore impl
    # ------------------------------------------------------------------

    async def write(self, insight: Insight) -> Insight:
        with tracer.start_as_current_span("pgvector_insight.write") as span:
            span.set_attribute("memory.insight_id", str(insight.id))
            span.set_attribute("memory.theme", insight.theme.value)
            try:
                async with self._pool.acquire() as conn:
                    await conn.execute(
                        """
                        INSERT INTO consolidation_insights
                            (id, theme, kind, narrative, supporting_experience_ids,
                             confidence, confidence_tier, synthesized_at,
                             narrative_embedding)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NULL)
                        ON CONFLICT (id) DO UPDATE SET
                            theme = EXCLUDED.theme,
                            kind = EXCLUDED.kind,
                            narrative = EXCLUDED.narrative,
                            supporting_experience_ids = EXCLUDED.supporting_experience_ids,
                            confidence = EXCLUDED.confidence,
                            confidence_tier = EXCLUDED.confidence_tier,
                            synthesized_at = EXCLUDED.synthesized_at
                        """,
                        insight.id,
                        insight.theme.value,
                        insight.kind.value,
                        insight.narrative,
                        list(insight.supporting_experience_ids),
                        insight.confidence,
                        insight.confidence_tier.value,
                        insight.synthesized_at,
                    )
                logger.info(
                    "pgvector_insight_stored",
                    insight_id=str(insight.id),
                    theme=insight.theme.value,
                    confidence=insight.confidence,
                )
                return insight
            except Exception as exc:
                span.record_exception(exc)
                logger.error("pgvector_insight_write_failed", error=str(exc))
                raise

    async def write_with_embedding(
        self,
        insight: Insight,
        embedding: list[float],
    ) -> Insight:
        """Variant that stores the narrative embedding alongside the row.

        Kept separate from ``write`` so the writer doesn't have to know
        about the embedder. The orchestrator can call this once the
        embedding service is wired without breaking back-compat for
        callers that only need the structural write.
        """
        with tracer.start_as_current_span("pgvector_insight.write_with_embedding") as span:
            span.set_attribute("memory.insight_id", str(insight.id))
            span.set_attribute("memory.embedding_dim", len(embedding))
            try:
                embedding_literal = _vector_literal(embedding)
                async with self._pool.acquire() as conn:
                    await conn.execute(
                        """
                        INSERT INTO consolidation_insights
                            (id, theme, kind, narrative, supporting_experience_ids,
                             confidence, confidence_tier, synthesized_at,
                             narrative_embedding)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::vector)
                        ON CONFLICT (id) DO UPDATE SET
                            theme = EXCLUDED.theme,
                            kind = EXCLUDED.kind,
                            narrative = EXCLUDED.narrative,
                            supporting_experience_ids = EXCLUDED.supporting_experience_ids,
                            confidence = EXCLUDED.confidence,
                            confidence_tier = EXCLUDED.confidence_tier,
                            synthesized_at = EXCLUDED.synthesized_at,
                            narrative_embedding = EXCLUDED.narrative_embedding
                        """,
                        insight.id,
                        insight.theme.value,
                        insight.kind.value,
                        insight.narrative,
                        list(insight.supporting_experience_ids),
                        insight.confidence,
                        insight.confidence_tier.value,
                        insight.synthesized_at,
                        embedding_literal,
                    )
                return insight
            except Exception as exc:
                span.record_exception(exc)
                logger.error("pgvector_insight_write_embedding_failed", error=str(exc))
                raise

    async def list(
        self,
        *,
        theme: ConsolidationTheme | None = None,
        limit: int = 50,
    ) -> list[Insight]:
        with tracer.start_as_current_span("pgvector_insight.list") as span:
            try:
                if theme is None:
                    query = (
                        "SELECT id, theme, kind, narrative, "
                        "supporting_experience_ids, confidence, "
                        "confidence_tier, synthesized_at "
                        "FROM consolidation_insights "
                        "ORDER BY synthesized_at DESC LIMIT $1"
                    )
                    params: tuple[Any, ...] = (limit,)
                else:
                    query = (
                        "SELECT id, theme, kind, narrative, "
                        "supporting_experience_ids, confidence, "
                        "confidence_tier, synthesized_at "
                        "FROM consolidation_insights "
                        "WHERE theme = $1 "
                        "ORDER BY synthesized_at DESC LIMIT $2"
                    )
                    params = (theme.value, limit)
                async with self._pool.acquire() as conn:
                    rows = await conn.fetch(query, *params)
                results = [_row_to_insight(row) for row in rows]
                span.set_attribute("memory.result_count", len(results))
                return results
            except Exception as exc:
                span.record_exception(exc)
                logger.error("pgvector_insight_list_failed", error=str(exc))
                raise

    async def get(self, insight_id: UUID) -> Insight | None:
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT id, theme, kind, narrative, supporting_experience_ids,
                           confidence, confidence_tier, synthesized_at
                    FROM consolidation_insights
                    WHERE id = $1
                    """,
                    insight_id,
                )
            if row is None:
                return None
            return _row_to_insight(row)
        except Exception as exc:
            logger.error("pgvector_insight_get_failed", error=str(exc))
            raise


def _row_to_insight(row: Any) -> Insight:
    supporting_raw = row["supporting_experience_ids"] or []
    supporting = [
        item if isinstance(item, UUID) else UUID(str(item))
        for item in supporting_raw
    ]
    synthesized_at = row["synthesized_at"]
    if synthesized_at is not None and synthesized_at.tzinfo is None:
        synthesized_at = synthesized_at.replace(tzinfo=UTC)
    return Insight(
        id=row["id"],
        theme=ConsolidationTheme(row["theme"]),
        kind=InsightKind(row["kind"]),
        narrative=row["narrative"],
        supporting_experience_ids=supporting,
        confidence=row["confidence"],
        confidence_tier=ConfidenceTier(row["confidence_tier"]),
        synthesized_at=synthesized_at if synthesized_at is not None else datetime.now(UTC),
    )


def _experience_ids_json(insight: Insight) -> str:
    """Helper for callers that want a JSON-stringified version of the IDs."""
    return json.dumps([str(uid) for uid in insight.supporting_experience_ids])
