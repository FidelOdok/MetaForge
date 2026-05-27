"""Event-bus subscriber that indexes ``AGENT_TASK_*`` events into the experience store.

Mirrors ``digital_twin.knowledge.consumer.KnowledgeConsumer`` but with
experience-specific transforms: importance-score filter, event→text
embedding, and idempotent replay via ``delete_by_run``.
"""

from __future__ import annotations

import structlog

from digital_twin.knowledge.embedding_service import EmbeddingService
from digital_twin.memory.event_embedder import EventEmbedder
from digital_twin.memory.importance import (
    DEFAULT_WEIGHTS,
    ImportanceWeights,
    score_importance,
)
from digital_twin.memory.store import ExperienceStore
from observability.tracing import get_tracer
from orchestrator.event_bus.events import Event, EventType
from orchestrator.event_bus.subscribers import EventSubscriber

logger = structlog.get_logger(__name__)
tracer = get_tracer("digital_twin.memory.consumer")

DEFAULT_MIN_IMPORTANCE = 0.20
"""Below this importance score, events are dropped instead of indexed.

``STARTED`` events without much payload generally land here. The threshold
keeps the experience store focused on signals worth retrieving later.
"""

_TERMINAL_TYPES = {EventType.AGENT_TASK_COMPLETED, EventType.AGENT_TASK_FAILED}


class ExperienceConsumer(EventSubscriber):
    """Subscribes to ``AGENT_TASK_*`` events, embeds them, writes to the store."""

    def __init__(
        self,
        store: ExperienceStore,
        embeddings: EmbeddingService,
        *,
        weights: ImportanceWeights = DEFAULT_WEIGHTS,
        min_importance: float = DEFAULT_MIN_IMPORTANCE,
        index_started_events: bool = False,
    ) -> None:
        self._store = store
        self._embeddings = embeddings
        self._embedder = EventEmbedder(embeddings)
        self._weights = weights
        self._min_importance = min_importance
        self._index_started_events = index_started_events
        self._started_runs: set[str] = set()

    @property
    def subscriber_id(self) -> str:
        return "memory.experience_consumer"

    @property
    def event_types(self) -> set[EventType] | None:
        return {
            EventType.AGENT_TASK_STARTED,
            EventType.AGENT_TASK_COMPLETED,
            EventType.AGENT_TASK_FAILED,
        }

    async def on_event(self, event: Event) -> None:
        with tracer.start_as_current_span("experience_consumer.on_event") as span:
            span.set_attribute("event.type", str(event.type))
            span.set_attribute("event.id", event.id)

            run_id = str(event.data.get("run_id", "")) if event.data else ""
            if not run_id:
                logger.debug(
                    "experience_consumer_skip",
                    event_id=event.id,
                    reason="missing_run_id",
                )
                return

            if event.type == EventType.AGENT_TASK_STARTED:
                # Started events arrive before the consumer knows the outcome.
                # Track the run so a replay can clear stale records first, but
                # only index the start record itself if explicitly enabled.
                self._started_runs.add(run_id)
                if not self._index_started_events:
                    return

            if event.type in _TERMINAL_TYPES and run_id in self._started_runs:
                # Idempotent replay: blow away the partial record(s) for this run.
                # New consumers / cold storage will see this as a no-op.
                try:
                    await self._store.delete_by_run(run_id)
                except Exception as exc:  # pragma: no cover — best effort
                    logger.warning(
                        "experience_consumer_predelete_failed",
                        run_id=run_id,
                        error=str(exc),
                    )
                self._started_runs.discard(run_id)

            score = score_importance(event, weights=self._weights)
            span.set_attribute("memory.importance.total", score.total)
            if score.total < self._min_importance:
                logger.debug(
                    "experience_consumer_skip_low_importance",
                    event_id=event.id,
                    score=score.total,
                    threshold=self._min_importance,
                )
                return

            try:
                experience = await self._embedder.build_experience(event, importance=score.total)
            except Exception as exc:
                span.record_exception(exc)
                logger.error(
                    "experience_consumer_build_failed",
                    event_id=event.id,
                    error=str(exc),
                )
                return

            try:
                stored = await self._store.store(experience)
            except Exception as exc:
                span.record_exception(exc)
                logger.error(
                    "experience_consumer_store_failed",
                    event_id=event.id,
                    error=str(exc),
                )
                return

            logger.info(
                "experience_consumer_indexed",
                event_id=event.id,
                experience_id=str(stored.id),
                importance=round(score.total, 3),
                agent_code=stored.agent_code,
                success=stored.success,
            )
