"""EventEmbedder — turn an ``AGENT_TASK_*`` event into an embedded experience (MET-458).

Extracted from ``ExperienceConsumer`` so the serialize → embed → assemble
step is a reusable, independently-testable unit. The consumer keeps the
concerns that are genuinely its own (subscription, importance gating,
idempotent replay, persistence); this class owns the transform:

1. ``event_to_text`` renders a canonical, deterministic string so the
   same event always embeds to the same vector.
2. the injected ``EmbeddingService`` produces the vector (provider-agnostic
   — local model in tests, a hosted embeddings API in production).
3. the result is assembled into an ``ExperienceMemory`` with citation
   metadata, ready for the experience store.

The embedder never persists — keeping a single write path in the
consumer avoids two ways to insert the same record.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog

from digital_twin.knowledge.embedding_service import EmbeddingService
from digital_twin.memory.embeddings import event_to_text
from digital_twin.memory.models import ConfidenceTier, ExperienceMemory
from observability.tracing import get_tracer
from orchestrator.event_bus.events import Event, EventType

logger = structlog.get_logger(__name__)
tracer = get_tracer("digital_twin.memory.event_embedder")


class EventEmbedder:
    """Serialize, embed, and assemble ``AGENT_TASK_*`` events into experiences."""

    def __init__(self, embeddings: EmbeddingService) -> None:
        self._embeddings = embeddings

    async def embed_event(self, event: Event) -> list[float]:
        """Render ``event`` to canonical text and embed it.

        The single "embed an event" primitive — used by
        ``build_experience`` and available to callers that only need the
        vector (e.g. an ad-hoc similarity probe).
        """
        with tracer.start_as_current_span("event_embedder.embed_event") as span:
            span.set_attribute("event.type", str(event.type))
            span.set_attribute("event.id", event.id)
            text = event_to_text(event)
            return await self._embeddings.embed(text)

    async def build_experience(
        self,
        event: Event,
        *,
        importance: float,
        confidence: ConfidenceTier = ConfidenceTier.VERBATIM,
    ) -> ExperienceMemory:
        """Build a fully-embedded ``ExperienceMemory`` from ``event``.

        Pure transform: serialize → embed → assemble. ``importance`` is
        supplied by the caller (the consumer scores it), keeping the
        embedder free of scoring policy. Does not persist.
        """
        with tracer.start_as_current_span("event_embedder.build_experience") as span:
            span.set_attribute("event.type", str(event.type))
            span.set_attribute("event.id", event.id)
            text = event_to_text(event)
            embedding = await self._embeddings.embed(text)
            data: dict[str, Any] = event.data or {}

            success = event.type == EventType.AGENT_TASK_COMPLETED
            error = data.get("error") if event.type == EventType.AGENT_TASK_FAILED else None

            return ExperienceMemory(
                run_id=str(data.get("run_id", "")),
                step_id=str(data.get("step_id", "")),
                agent_code=str(data.get("agent_code", "")),
                task_type=str(data.get("task_type", "") or ""),
                success=success,
                duration_seconds=_coerce_float(data.get("duration")),
                result_summary=text,
                error=str(error) if error else None,
                project_id=_coerce_uuid(data.get("project_id")),
                timestamp=_parse_timestamp(event.timestamp),
                importance=importance,
                confidence=confidence,
                embedding=embedding,
                metadata={
                    "event_id": event.id,
                    "event_type": str(event.type),
                    "source": event.source,
                },
            )


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_uuid(value: Any) -> UUID | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


def _parse_timestamp(value: str) -> datetime:
    try:
        ts = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return datetime.now(UTC)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts
