"""High-level client for the agent memory layer.

The client wraps an ``ExperienceStore`` and an ``EmbeddingService`` so
callers — MCP tools, REST endpoints, the SDK — never touch the embedder
or the store directly. Embedding the query string is a write-side
concern that this object owns.
"""

from __future__ import annotations

from uuid import UUID

import structlog

from digital_twin.knowledge.embedding_service import EmbeddingService
from digital_twin.memory.models import MemorySearchHit
from digital_twin.memory.store import ExperienceStore
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("digital_twin.memory.client")

DEFAULT_RETRIEVAL_LIMIT = 5
MAX_RETRIEVAL_LIMIT = 50


class MemoryClient:
    """Read-side facade over the experience store."""

    def __init__(
        self,
        store: ExperienceStore,
        embeddings: EmbeddingService,
    ) -> None:
        self._store = store
        self._embeddings = embeddings

    async def retrieve_similar_experience(
        self,
        goal: str,
        *,
        limit: int = DEFAULT_RETRIEVAL_LIMIT,
        project_id: UUID | None = None,
        agent_code: str | None = None,
        only_success: bool | None = None,
        min_similarity: float | None = None,
    ) -> list[MemorySearchHit]:
        """Return experiences most similar to ``goal``, ranked by similarity.

        ``goal`` is a natural-language description of what the caller is
        trying to do. The client embeds it with the same service the
        consumer uses, then queries the store for nearest neighbours
        (already ordered by descending cosine similarity).

        ``min_similarity`` (MET-460) is the retrieval-confidence floor: in
        a semantic search the cosine similarity *is* the per-result
        confidence, so hits below the threshold are dropped to keep weak,
        loosely-related experiences out of the result. ``None`` (default)
        applies no floor. Because the store returns results sorted by
        similarity, filtering the top-``limit`` slice never hides a
        qualifying hit that would otherwise fit within the limit.
        """
        if not goal or not goal.strip():
            return []
        capped_limit = max(1, min(limit, MAX_RETRIEVAL_LIMIT))

        with tracer.start_as_current_span("memory_client.retrieve") as span:
            span.set_attribute("memory.limit", capped_limit)
            span.set_attribute("memory.has_project_filter", project_id is not None)
            span.set_attribute("memory.has_agent_filter", agent_code is not None)
            span.set_attribute("memory.has_similarity_floor", min_similarity is not None)
            embedding = await self._embeddings.embed(goal.strip())
            hits = await self._store.search(
                embedding,
                limit=capped_limit,
                project_id=project_id,
                agent_code=agent_code,
                only_success=only_success,
            )
            if min_similarity is not None:
                hits = [hit for hit in hits if hit.similarity >= min_similarity]
            span.set_attribute("memory.result_count", len(hits))
            logger.info(
                "memory_retrieve_completed",
                goal_length=len(goal),
                result_count=len(hits),
                project_id=str(project_id) if project_id else None,
                agent_code=agent_code,
                min_similarity=min_similarity,
            )
            return hits
