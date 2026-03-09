"""Handler for the ingest_knowledge skill."""

from __future__ import annotations

import structlog

from observability.tracing import get_tracer
from skill_registry.skill_base import SkillBase
from twin_core.knowledge.models import KnowledgeType
from twin_core.knowledge.store import KnowledgeStore

from .schema import IngestKnowledgeInput, IngestKnowledgeOutput

logger = structlog.get_logger(__name__)
tracer = get_tracer("skill.ingest_knowledge")


class IngestKnowledgeHandler(SkillBase[IngestKnowledgeInput, IngestKnowledgeOutput]):
    """Ingests text content into the knowledge store with chunking and embedding."""

    input_type = IngestKnowledgeInput
    output_type = IngestKnowledgeOutput

    def __init__(self, context: object, knowledge_store: KnowledgeStore) -> None:
        super().__init__(context)  # type: ignore[arg-type]
        self._store = knowledge_store

    async def execute(self, input_data: IngestKnowledgeInput) -> IngestKnowledgeOutput:
        """Chunk and ingest content into the knowledge store."""
        with tracer.start_as_current_span("ingest_knowledge.execute") as span:
            span.set_attribute("skill.name", "ingest_knowledge")
            span.set_attribute("knowledge.content_length", len(input_data.content))
            span.set_attribute("knowledge.type", input_data.knowledge_type)

            # Resolve knowledge type
            try:
                knowledge_type = KnowledgeType(input_data.knowledge_type)
            except ValueError:
                self.logger.warning(
                    "Unknown knowledge_type, defaulting to GENERAL",
                    knowledge_type=input_data.knowledge_type,
                )
                knowledge_type = KnowledgeType.GENERAL

            self.logger.info(
                "Ingesting knowledge",
                content_length=len(input_data.content),
                knowledge_type=knowledge_type.value,
                source=input_data.source,
            )

            metadata = dict(input_data.metadata) if input_data.metadata else {}

            # Use chunked ingestion for long content, single ingestion for short
            entries = await self._store.ingest_chunked(
                content=input_data.content,
                knowledge_type=knowledge_type,
                source=input_data.source,
                metadata=metadata,
            )

            # The first entry is the "primary" entry
            primary = entries[0]
            embedded = primary.has_embedding

            self.logger.info(
                "Knowledge ingestion completed",
                entry_id=str(primary.id),
                chunk_count=len(entries),
                embedded=embedded,
            )

            return IngestKnowledgeOutput(
                entry_id=str(primary.id),
                embedded=embedded,
                chunk_count=len(entries),
                content_length=len(input_data.content),
            )
