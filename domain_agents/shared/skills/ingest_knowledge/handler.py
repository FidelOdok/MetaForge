"""Handler for the ingest_knowledge skill."""

from __future__ import annotations

import structlog

from digital_twin.knowledge.embedding_service import EmbeddingService
from digital_twin.knowledge.store import KnowledgeEntry, KnowledgeStore, KnowledgeType
from observability.tracing import get_tracer
from skill_registry.skill_base import SkillBase

from .schema import IngestKnowledgeInput, IngestKnowledgeOutput

logger = structlog.get_logger(__name__)
tracer = get_tracer("skill.ingest_knowledge")

# Chunking parameters
CHUNK_SIZE = 512
CHUNK_OVERLAP = 64


def _chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping chunks by character count."""
    if len(text) <= chunk_size:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start = end - overlap
    return chunks


class IngestKnowledgeHandler(SkillBase[IngestKnowledgeInput, IngestKnowledgeOutput]):
    """Ingests text content into the knowledge store with chunking and embedding."""

    input_type = IngestKnowledgeInput
    output_type = IngestKnowledgeOutput

    def __init__(
        self,
        context: object,
        knowledge_store: KnowledgeStore,
        embedding_service: EmbeddingService | None = None,
    ) -> None:
        super().__init__(context)  # type: ignore[arg-type]
        self._store = knowledge_store
        self._embedding_service = embedding_service

    async def execute(self, input_data: IngestKnowledgeInput) -> IngestKnowledgeOutput:
        """Chunk and ingest content into the knowledge store."""
        with tracer.start_as_current_span("ingest_knowledge.execute") as span:
            span.set_attribute("skill.name", "ingest_knowledge")
            span.set_attribute("knowledge.content_length", len(input_data.content))
            span.set_attribute("knowledge.type", str(input_data.knowledge_type))

            # The schema rejects a type outside the store's enum; an unknown
            # type used to be filed silently as design_decision (FORGE-552).
            knowledge_type = KnowledgeType(input_data.knowledge_type)

            self.logger.info(
                "Ingesting knowledge",
                content_length=len(input_data.content),
                knowledge_type=knowledge_type.value,
                source_path=input_data.source_path,
            )

            metadata = dict(input_data.metadata) if input_data.metadata else {}

            # Chunk the content
            chunks = _chunk_text(input_data.content)
            entries: list[KnowledgeEntry] = []

            for index, chunk in enumerate(chunks):
                # Generate embedding if service available
                embedding: list[float] = []
                if self._embedding_service is not None:
                    embedding = await self._embedding_service.embed(chunk)

                entry = KnowledgeEntry(
                    content=chunk,
                    embedding=embedding,
                    knowledge_type=knowledge_type,
                    metadata=metadata,
                    source_path=input_data.source_path,
                    chunk_index=index,
                    total_chunks=len(chunks),
                )
                stored = await self._store.store(entry)
                entries.append(stored)

            primary = entries[0]

            self.logger.info(
                "Knowledge ingestion completed",
                entry_id=str(primary.id),
                chunk_count=len(entries),
                embedded=bool(primary.embedding),
            )

            return IngestKnowledgeOutput(
                entry_id=str(primary.id),
                embedded=bool(primary.embedding),
                chunk_count=len(entries),
                content_length=len(input_data.content),
            )
