"""Knowledge consumer — subscribes to event bus and indexes work_products.

Listens for WORK_PRODUCT_CREATED and WORK_PRODUCT_UPDATED events, extracts
textual content, embeds it, and stores the result in the knowledge store.
"""

from __future__ import annotations

from typing import Any

import structlog

from digital_twin.knowledge.embedding_service import EmbeddingService
from digital_twin.knowledge.store import KnowledgeEntry, KnowledgeStore, KnowledgeType
from observability.tracing import get_tracer
from orchestrator.event_bus.events import Event, EventType
from orchestrator.event_bus.subscribers import EventSubscriber

logger = structlog.get_logger(__name__)
tracer = get_tracer("digital_twin.knowledge.consumer")

# Maps work_product type strings to KnowledgeType for auto-classification.
_WORK_PRODUCT_TYPE_MAP: dict[str, KnowledgeType] = {
    "design_decision": KnowledgeType.DESIGN_DECISION,
    "component": KnowledgeType.COMPONENT,
    "constraint": KnowledgeType.CONSTRAINT,
    "failure_mode": KnowledgeType.FAILURE,
    "session": KnowledgeType.SESSION,
}


class KnowledgeConsumer(EventSubscriber):
    """Event bus subscriber that indexes work_product events into the knowledge store."""

    def __init__(
        self,
        store: KnowledgeStore,
        embedding_service: EmbeddingService,
    ) -> None:
        self._store = store
        self._embedding = embedding_service

    @property
    def subscriber_id(self) -> str:
        return "knowledge_consumer"

    @property
    def event_types(self) -> set[EventType] | None:
        return {EventType.WORK_PRODUCT_CREATED, EventType.WORK_PRODUCT_UPDATED}

    async def on_event(self, event: Event) -> None:
        """Handle an work_product event by extracting, embedding, and storing content."""
        with tracer.start_as_current_span("knowledge_consumer.on_event") as span:
            span.set_attribute("event.type", str(event.type))
            span.set_attribute("event.id", event.id)
            try:
                content = self._extract_content(event.data)
                if not content:
                    logger.debug(
                        "knowledge_consumer_skip",
                        event_id=event.id,
                        reason="no_content",
                    )
                    return

                knowledge_type = self._classify(event.data)
                embedding = await self._embedding.embed(content)

                source_id = event.data.get("work_product_id")
                entry = KnowledgeEntry(
                    content=content,
                    embedding=embedding,
                    knowledge_type=knowledge_type,
                    metadata={
                        "event_id": event.id,
                        "event_type": str(event.type),
                        "source": event.source,
                    },
                    source_work_product_id=source_id,
                )
                await self._store.store(entry)

                logger.info(
                    "knowledge_consumer_indexed",
                    entry_id=str(entry.id),
                    event_id=event.id,
                    knowledge_type=str(knowledge_type),
                )
            except Exception as exc:
                span.record_exception(exc)
                logger.error(
                    "knowledge_consumer_error",
                    event_id=event.id,
                    error=str(exc),
                )

    async def ingest_batch(self, items: list[dict[str, Any]]) -> int:
        """Batch-ingest a list of content dicts.

        Each item should have at least ``content`` and ``knowledge_type`` keys.
        Returns the number of successfully ingested items.
        """
        with tracer.start_as_current_span("knowledge_consumer.ingest_batch") as span:
            span.set_attribute("batch.size", len(items))
            texts = [item.get("content", "") for item in items]
            embeddings = await self._embedding.embed_batch(texts)

            ingested = 0
            for item, emb in zip(items, embeddings):
                content = item.get("content", "")
                if not content:
                    continue
                kt_str = item.get("knowledge_type", "session")
                knowledge_type = KnowledgeType(kt_str)
                entry = KnowledgeEntry(
                    content=content,
                    embedding=emb,
                    knowledge_type=knowledge_type,
                    metadata=item.get("metadata", {}),
                    source_work_product_id=item.get("source_work_product_id"),
                )
                await self._store.store(entry)
                ingested += 1

            span.set_attribute("batch.ingested", ingested)
            logger.info("knowledge_batch_ingested", count=ingested, total=len(items))
            return ingested

    # -- Helpers -----------------------------------------------------------

    @staticmethod
    def _extract_content(data: dict[str, Any]) -> str:
        """Extract textual content from event data."""
        # Try common content fields
        for key in ("content", "description", "name", "summary", "text"):
            val = data.get(key)
            if val and isinstance(val, str):
                return val
        # Fall back to a string representation of the data
        props = data.get("properties", {})
        if props and isinstance(props, dict):
            parts = [f"{k}: {v}" for k, v in props.items() if isinstance(v, str)]
            if parts:
                return "; ".join(parts)
        return ""

    @staticmethod
    def _classify(data: dict[str, Any]) -> KnowledgeType:
        """Classify knowledge type from event data."""
        work_product_type = data.get("work_product_type", "")
        return _WORK_PRODUCT_TYPE_MAP.get(work_product_type, KnowledgeType.SESSION)
