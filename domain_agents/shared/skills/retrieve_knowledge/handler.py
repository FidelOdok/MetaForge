"""Handler for the retrieve_knowledge skill."""

from __future__ import annotations

import structlog

from digital_twin.knowledge.embedding_service import EmbeddingService
from digital_twin.knowledge.store import KnowledgeStore
from observability.tracing import get_tracer
from skill_registry.skill_base import SkillBase
from twin_core.knowledge.models import KnowledgeType

from .schema import KnowledgeResult, RetrieveKnowledgeInput, RetrieveKnowledgeOutput

logger = structlog.get_logger(__name__)
tracer = get_tracer("skill.retrieve_knowledge")


class RetrieveKnowledgeHandler(SkillBase[RetrieveKnowledgeInput, RetrieveKnowledgeOutput]):
    """Searches the knowledge store using semantic similarity."""

    input_type = RetrieveKnowledgeInput
    output_type = RetrieveKnowledgeOutput

    def __init__(
        self,
        context: object,
        knowledge_store: KnowledgeStore,
        embedding_service: EmbeddingService | None = None,
    ) -> None:
        super().__init__(context)  # type: ignore[arg-type]
        self._store = knowledge_store
        self._embedding_service = embedding_service

    async def execute(self, input_data: RetrieveKnowledgeInput) -> RetrieveKnowledgeOutput:
        """Execute semantic search over the knowledge store."""
        with tracer.start_as_current_span("retrieve_knowledge.execute") as span:
            span.set_attribute("skill.name", "retrieve_knowledge")
            span.set_attribute("knowledge.query_length", len(input_data.query))
            span.set_attribute("knowledge.limit", input_data.limit)

            # Resolve optional knowledge_type filter
            knowledge_type_filter: KnowledgeType | None = None
            if input_data.knowledge_type:
                try:
                    knowledge_type_filter = KnowledgeType(input_data.knowledge_type)
                except ValueError:
                    self.logger.warning(
                        "Unknown knowledge_type filter, searching all types",
                        knowledge_type=input_data.knowledge_type,
                    )

            self.logger.info(
                "Searching knowledge store",
                query=input_data.query[:100],
                knowledge_type=input_data.knowledge_type,
                limit=input_data.limit,
            )

            # Embed the query text, then search by embedding vector
            if self._embedding_service is not None:
                query_embedding = await self._embedding_service.embed(input_data.query)
            else:
                # Fallback: use a zero vector (will match nothing meaningfully)
                query_embedding = [0.0] * 384

            search_results = await self._store.search(
                embedding=query_embedding,
                knowledge_type=knowledge_type_filter,
                limit=input_data.limit,
            )

            results = [
                KnowledgeResult(
                    entry_id=str(entry.id),
                    content=entry.content,
                    knowledge_type=str(entry.knowledge_type),
                    source=str(entry.source_work_product_id)
                    if entry.source_work_product_id
                    else "",
                    score=0.0,
                    metadata=entry.metadata or {},
                )
                for entry in search_results
            ]

            self.logger.info(
                "Knowledge search completed",
                results_count=len(results),
            )

            return RetrieveKnowledgeOutput(
                results=results,
                query=input_data.query,
                total_results=len(results),
            )
