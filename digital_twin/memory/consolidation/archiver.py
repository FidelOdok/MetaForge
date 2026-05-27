"""Stage 6 of the consolidation pipeline — archive consolidated experiences.

Once a batch of experiences has been synthesized into durable insights
(stages 1-5), the raw experiences no longer need to sit in hot memory:
the lesson is captured. This stage *moves* them to cold storage and
clears them from the hot ``ExperienceStore`` — active forgetting that
keeps the working set small without losing the audit trail.

Integrity is the contract: an experience is only deleted from the hot
store **after** it has been successfully written to the archive. If the
archive write fails the batch is left untouched (nothing deleted), so a
transient cold-storage outage never destroys data. A per-row delete
failure after a successful archive is logged but not fatal — the data is
safe in the archive either way.

The archive sink is provider-agnostic via the ``ExperienceArchive``
protocol. ``InMemoryExperienceArchive`` is the test / local-dev backend;
an S3-backed adapter is a follow-up (optional ``boto3`` dependency,
mirroring how the pgvector / neo4j stores are layered).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from uuid import UUID

import structlog

from digital_twin.memory.models import ExperienceMemory
from digital_twin.memory.store import ExperienceStore
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("digital_twin.memory.consolidation.archiver")


class ExperienceArchive(ABC):
    """Cold-storage sink for archived experiences (S3 in production)."""

    @abstractmethod
    async def archive(self, experiences: Sequence[ExperienceMemory]) -> int:
        """Persist ``experiences`` to cold storage; return the count written."""


class InMemoryExperienceArchive(ExperienceArchive):
    """Dict-backed archive for tests and local dev."""

    def __init__(self) -> None:
        self._archived: dict[UUID, ExperienceMemory] = {}

    async def archive(self, experiences: Sequence[ExperienceMemory]) -> int:
        for experience in experiences:
            self._archived[experience.id] = experience
        return len(experiences)

    @property
    def archived(self) -> list[ExperienceMemory]:
        return list(self._archived.values())

    def __contains__(self, experience_id: UUID) -> bool:
        return experience_id in self._archived


@dataclass(frozen=True)
class ArchiveResult:
    """Outcome of one archival pass."""

    archived_count: int = 0
    deleted_count: int = 0
    failed_delete_ids: tuple[UUID, ...] = field(default_factory=tuple)


class EventArchiver:
    """Move consolidated experiences to cold storage, then clear hot memory."""

    def __init__(self, store: ExperienceStore, archive: ExperienceArchive) -> None:
        self._store = store
        self._archive = archive

    async def archive_experiences(
        self,
        experiences: Sequence[ExperienceMemory],
    ) -> ArchiveResult:
        """Archive ``experiences`` to cold storage, then delete from the hot store.

        Returns an :class:`ArchiveResult`. Empty input is a no-op. If the
        cold-storage write fails the hot store is left untouched (nothing
        is deleted), preserving integrity.
        """
        batch = list(experiences)
        if not batch:
            return ArchiveResult()

        with tracer.start_as_current_span("consolidation.archiver.archive") as span:
            span.set_attribute("memory.archive_batch", len(batch))
            try:
                archived_count = await self._archive.archive(batch)
            except Exception as exc:
                span.record_exception(exc)
                logger.error(
                    "consolidation_archive_failed",
                    batch_size=len(batch),
                    error=str(exc),
                )
                # Integrity: nothing archived → delete nothing.
                return ArchiveResult()

            deleted = 0
            failed: list[UUID] = []
            for experience in batch:
                try:
                    if await self._store.delete(experience.id):
                        deleted += 1
                except Exception as exc:  # pragma: no cover — best effort
                    span.record_exception(exc)
                    failed.append(experience.id)
                    logger.warning(
                        "consolidation_archive_cleanup_failed",
                        experience_id=str(experience.id),
                        error=str(exc),
                    )

            result = ArchiveResult(
                archived_count=archived_count,
                deleted_count=deleted,
                failed_delete_ids=tuple(failed),
            )
            span.set_attribute("memory.archived", result.archived_count)
            span.set_attribute("memory.deleted", result.deleted_count)
            logger.info(
                "consolidation_archive_completed",
                archived=result.archived_count,
                deleted=result.deleted_count,
                failed_deletes=len(result.failed_delete_ids),
            )
            return result
