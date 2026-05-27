"""High-level client for the agent memory layer.

The client wraps an ``ExperienceStore`` and an ``EmbeddingService`` so
callers — MCP tools, REST endpoints, the SDK — never touch the embedder
or the store directly. Embedding the query string is a write-side
concern that this object owns.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

import structlog

from digital_twin.knowledge.embedding_service import EmbeddingService
from digital_twin.knowledge.types import KnowledgeType
from digital_twin.memory.models import MemorySearchHit
from digital_twin.memory.store import ExperienceStore
from observability.tracing import get_tracer

if TYPE_CHECKING:
    from digital_twin.knowledge.service import KnowledgeService, SearchHit

logger = structlog.get_logger(__name__)
tracer = get_tracer("digital_twin.memory.client")

DEFAULT_RETRIEVAL_LIMIT = 5
MAX_RETRIEVAL_LIMIT = 50


class MemoryClient:
    """Read-side facade over the experience store.

    Wraps the experience store + embedder for ``retrieve_similar_experience``.
    When a ``knowledge_service`` is supplied it also exposes two L1
    knowledge-backed convenience methods (MET-464): ``search_design_rationale``
    and ``get_component_context``, which are typed semantic searches over the
    design-decision and component knowledge respectively.
    """

    def __init__(
        self,
        store: ExperienceStore,
        embeddings: EmbeddingService,
        *,
        knowledge_service: KnowledgeService | None = None,
    ) -> None:
        self._store = store
        self._embeddings = embeddings
        self._knowledge = knowledge_service

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

    async def search_design_rationale(
        self,
        query: str,
        *,
        limit: int = DEFAULT_RETRIEVAL_LIMIT,
        project_id: UUID | None = None,
    ) -> list[SearchHit]:
        """Semantic search over recorded design decisions / rationale (MET-464).

        A typed convenience over the L1 knowledge base: searches knowledge
        of type ``DESIGN_DECISION`` so callers can ask "why was X decided?"
        and get attributable hits. Requires a ``knowledge_service`` wired at
        construction. Empty query returns ``[]``.
        """
        return await self._knowledge_search(
            query, KnowledgeType.DESIGN_DECISION, limit=limit, project_id=project_id
        )

    async def get_component_context(
        self,
        name: str,
        *,
        limit: int = DEFAULT_RETRIEVAL_LIMIT,
        project_id: UUID | None = None,
    ) -> list[SearchHit]:
        """Return component usage / relationship knowledge for ``name`` (MET-464).

        A typed convenience over the L1 knowledge base: searches knowledge of
        type ``COMPONENT`` for the named part, surfacing its captured usage
        history and relationships. Requires a ``knowledge_service`` wired at
        construction. Empty name returns ``[]``.
        """
        return await self._knowledge_search(
            name, KnowledgeType.COMPONENT, limit=limit, project_id=project_id
        )

    async def _knowledge_search(
        self,
        query: str,
        knowledge_type: KnowledgeType,
        *,
        limit: int,
        project_id: UUID | None,
    ) -> list[SearchHit]:
        if not query or not query.strip():
            return []
        if self._knowledge is None:
            raise RuntimeError(
                "MemoryClient knowledge methods require a knowledge_service; "
                "construct with MemoryClient(store, embeddings, knowledge_service=...)."
            )
        capped_limit = max(1, min(limit, MAX_RETRIEVAL_LIMIT))
        with tracer.start_as_current_span("memory_client.knowledge_search") as span:
            span.set_attribute("memory.knowledge_type", knowledge_type.value)
            span.set_attribute("memory.limit", capped_limit)
            hits = await self._knowledge.search(
                query.strip(),
                top_k=capped_limit,
                knowledge_type=knowledge_type,
                project_id=project_id,
            )
            span.set_attribute("memory.result_count", len(hits))
            logger.info(
                "memory_knowledge_search_completed",
                knowledge_type=knowledge_type.value,
                result_count=len(hits),
                project_id=str(project_id) if project_id else None,
            )
            return hits
