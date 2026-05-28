"""Concrete :class:`ExperienceRecorder` backed by the experience store.

Owned by the digital_twin layer because it knows about embeddings and
the pgvector store. The gateway constructs one of these at startup
and injects it into each domain agent (Mechanical, Electronics, ...).

The recorder is intentionally fail-soft: any failure to embed or
write is logged and swallowed, so a flaky memory backend cannot break
the agent task that produced the event.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import structlog

from digital_twin.knowledge.embedding_service import EmbeddingService
from digital_twin.memory.models import ConfidenceTier, ExperienceMemory
from digital_twin.memory.store import ExperienceStore
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("digital_twin.memory.experience_recorder")


class MemoryExperienceRecorder:
    """Adapter that turns an agent's task summary into an indexed row.

    Embeds ``result_summary`` via :class:`EmbeddingService`, builds an
    :class:`ExperienceMemory`, and persists it via the
    :class:`ExperienceStore`. The agent never sees any of this — it
    only knows the ``ExperienceRecorder`` Protocol over in
    ``domain_agents/shared/``.
    """

    def __init__(
        self,
        store: ExperienceStore,
        embeddings: EmbeddingService,
    ) -> None:
        self._store = store
        self._embeddings = embeddings

    async def record(
        self,
        *,
        run_id: str,
        step_id: str,
        agent_code: str,
        task_type: str,
        success: bool,
        duration_seconds: float,
        result_summary: str,
        error: str | None = None,
        project_id: UUID | None = None,
        importance: float = 0.5,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Persist one experience row. Never raises."""
        with tracer.start_as_current_span("memory_recorder.record") as span:
            span.set_attribute("memory.agent_code", agent_code)
            span.set_attribute("memory.task_type", task_type)
            span.set_attribute("memory.success", success)
            try:
                embedding = await self._embeddings.embed(result_summary.strip() or task_type)
            except Exception as exc:  # noqa: BLE001
                span.record_exception(exc)
                logger.warning(
                    "memory_recorder_embed_failed",
                    error=str(exc),
                    agent_code=agent_code,
                    task_type=task_type,
                )
                return

            experience = ExperienceMemory(
                id=uuid4(),
                run_id=run_id,
                step_id=step_id,
                agent_code=agent_code,
                task_type=task_type,
                success=success,
                duration_seconds=duration_seconds,
                result_summary=result_summary,
                error=error,
                project_id=project_id,
                timestamp=datetime.now(UTC),
                importance=importance,
                # ``VERBATIM`` because the row is a literal copy of the
                # agent's own structured output — no LLM synthesis here.
                # Tier-3.5 consolidation will produce LLM_INFERRED rows
                # later when it synthesises across multiple experiences.
                confidence=ConfidenceTier.VERBATIM,
                embedding=embedding,
                metadata=metadata or {},
            )

            try:
                await self._store.store(experience)
                logger.info(
                    "memory_recorder_stored",
                    experience_id=str(experience.id),
                    agent_code=agent_code,
                    task_type=task_type,
                    success=success,
                )
            except Exception as exc:  # noqa: BLE001
                span.record_exception(exc)
                logger.warning(
                    "memory_recorder_store_failed",
                    error=str(exc),
                    agent_code=agent_code,
                    task_type=task_type,
                )
