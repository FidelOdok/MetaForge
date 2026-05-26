"""Experience storage backends for the memory layer.

Mirrors the ``KnowledgeStore`` pattern in ``digital_twin/knowledge/store.py``
but with experience-specific filters (project_id, agent_code, success) so
``retrieve_similar_experience`` does not have to scan every record.

The ``InMemoryExperienceStore`` is the test/dev backend. A pgvector
implementation lands in a later iteration once we have an integration
test that exercises it end-to-end.
"""

from __future__ import annotations

import math
import time
from abc import ABC, abstractmethod
from collections.abc import Iterable
from uuid import UUID

import structlog

from digital_twin.memory.models import ExperienceMemory, MemorySearchHit
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("digital_twin.memory.store")


class ExperienceStore(ABC):
    """Abstract storage for ``ExperienceMemory`` records."""

    @abstractmethod
    async def store(self, experience: ExperienceMemory) -> ExperienceMemory:
        """Persist an experience and return it with any generated fields."""

    @abstractmethod
    async def search(
        self,
        embedding: list[float],
        *,
        limit: int = 5,
        project_id: UUID | None = None,
        agent_code: str | None = None,
        only_success: bool | None = None,
    ) -> list[MemorySearchHit]:
        """Return the closest experiences by cosine similarity."""

    @abstractmethod
    async def get(self, experience_id: UUID) -> ExperienceMemory | None:
        """Retrieve a single experience by ID."""

    @abstractmethod
    async def delete(self, experience_id: UUID) -> bool:
        """Delete an experience. Returns ``True`` if it existed."""

    @abstractmethod
    async def delete_by_run(self, run_id: str) -> int:
        """Delete every experience tied to a workflow ``run_id``.

        Used by ``ExperienceConsumer`` to keep replays idempotent — a
        rerun of the same workflow first drops its prior experiences
        before re-indexing.
        """


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors; ``0.0`` for incompatible shapes."""
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0.0 or mag_b == 0.0:
        return 0.0
    return dot / (mag_a * mag_b)


class InMemoryExperienceStore(ExperienceStore):
    """Dict-backed experience store for development and tests."""

    def __init__(self) -> None:
        self._experiences: dict[UUID, ExperienceMemory] = {}

    async def store(self, experience: ExperienceMemory) -> ExperienceMemory:
        with tracer.start_as_current_span("experience_store.store") as span:
            span.set_attribute("experience.id", str(experience.id))
            span.set_attribute("experience.agent_code", experience.agent_code)
            self._experiences[experience.id] = experience
            logger.info(
                "experience_stored",
                experience_id=str(experience.id),
                agent_code=experience.agent_code,
                success=experience.success,
            )
            return experience

    async def search(
        self,
        embedding: list[float],
        *,
        limit: int = 5,
        project_id: UUID | None = None,
        agent_code: str | None = None,
        only_success: bool | None = None,
    ) -> list[MemorySearchHit]:
        with tracer.start_as_current_span("experience_store.search") as span:
            span.set_attribute("memory.top_k", limit)
            span.set_attribute("memory.query_embedding_dim", len(embedding))
            t0 = time.monotonic()

            candidates = _filter(
                self._experiences.values(),
                project_id=project_id,
                agent_code=agent_code,
                only_success=only_success,
            )
            scored = [
                (exp, _cosine_similarity(embedding, exp.embedding))
                for exp in candidates
                if exp.embedding
            ]
            scored.sort(key=lambda pair: pair[1], reverse=True)
            hits = [
                MemorySearchHit(experience=exp, similarity=sim, rank=rank)
                for rank, (exp, sim) in enumerate(scored[:limit])
            ]

            elapsed_ms = (time.monotonic() - t0) * 1000.0
            span.set_attribute("memory.result_count", len(hits))
            logger.info(
                "experience_search_completed",
                result_count=len(hits),
                project_id=str(project_id) if project_id else None,
                agent_code=agent_code,
                duration_ms=round(elapsed_ms, 2),
            )
            return hits

    async def get(self, experience_id: UUID) -> ExperienceMemory | None:
        return self._experiences.get(experience_id)

    async def delete(self, experience_id: UUID) -> bool:
        removed = self._experiences.pop(experience_id, None)
        if removed is not None:
            logger.info("experience_deleted", experience_id=str(experience_id))
            return True
        return False

    async def delete_by_run(self, run_id: str) -> int:
        with tracer.start_as_current_span("experience_store.delete_by_run") as span:
            span.set_attribute("memory.run_id", run_id)
            to_delete = [
                exp.id for exp in self._experiences.values() if exp.run_id == run_id
            ]
            for exp_id in to_delete:
                self._experiences.pop(exp_id, None)
            if to_delete:
                logger.info(
                    "experience_run_deleted",
                    run_id=run_id,
                    deleted=len(to_delete),
                )
            return len(to_delete)


def _filter(
    experiences: Iterable[ExperienceMemory],
    *,
    project_id: UUID | None,
    agent_code: str | None,
    only_success: bool | None,
) -> list[ExperienceMemory]:
    out: list[ExperienceMemory] = []
    for exp in experiences:
        if project_id is not None and exp.project_id != project_id:
            continue
        if agent_code is not None and exp.agent_code != agent_code:
            continue
        if only_success is not None and exp.success != only_success:
            continue
        out.append(exp)
    return out
