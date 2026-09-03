"""Stage 1 of the consolidation pipeline — fetch a batch of experiences.

Production pulls events from Kafka via the existing
``ExperienceConsumer`` writes-into-pgvector chain (the consolidation
worker reads from the same store rather than re-subscribing to the
topic). The in-memory fetcher in this module is the test backend and
the local-dev fallback when Kafka isn't wired.

Filters mirror the surface ``retrieve_similar_experience`` exposes —
project_id, agent_code, success — plus a minimum-importance threshold
so the consolidation pass doesn't waste LLM budget on noise.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID

import structlog

from digital_twin.memory.models import ExperienceMemory
from digital_twin.memory.store import ExperienceStore

logger = structlog.get_logger(__name__)


DEFAULT_FETCH_LIMIT = 500
DEFAULT_MIN_IMPORTANCE = 0.30
"""Slightly higher than the ingest-side floor (0.20) — the consolidation
pass deliberately drops marginal events so synthesis focuses on signal."""


class EventFetcher(ABC):
    """Abstract source of ``ExperienceMemory`` rows for consolidation."""

    @abstractmethod
    async def fetch(
        self,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        project_id: UUID | None = None,
        min_importance: float = DEFAULT_MIN_IMPORTANCE,
        limit: int = DEFAULT_FETCH_LIMIT,
    ) -> list[ExperienceMemory]:
        """Return a batch of experiences for the consolidation pass."""


class InMemoryEventFetcher(EventFetcher):
    """Reads experiences from an ``ExperienceStore`` (no Kafka, no S3).

    The store doesn't expose a "list-all" method by design — search is
    embedding-driven — so this fetcher reaches into the
    ``InMemoryExperienceStore`` private dict. That dict only exists on the
    in-memory adapter, so a pgvector deployment must use
    :class:`PgVectorEventFetcher` instead (MET-567) — pointing this fetcher
    at ``PgVectorExperienceStore`` silently returns an empty batch forever.
    """

    def __init__(self, store: ExperienceStore) -> None:
        self._store = store

    async def fetch(
        self,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        project_id: UUID | None = None,
        min_importance: float = DEFAULT_MIN_IMPORTANCE,
        limit: int = DEFAULT_FETCH_LIMIT,
    ) -> list[ExperienceMemory]:
        candidates = self._snapshot()
        out: list[ExperienceMemory] = []
        for exp in candidates:
            if since is not None and exp.timestamp < since:
                continue
            if until is not None and exp.timestamp > until:
                continue
            if project_id is not None and exp.project_id != project_id:
                continue
            if exp.importance < min_importance:
                continue
            out.append(exp)

        out.sort(key=lambda exp: exp.timestamp, reverse=True)
        capped = out[: max(0, limit)]
        logger.info(
            "consolidation_fetch_completed",
            candidate_count=len(candidates),
            returned=len(capped),
            project_id=str(project_id) if project_id else None,
            since=since.isoformat() if since else None,
            until=until.isoformat() if until else None,
            min_importance=min_importance,
        )
        return capped

    def _snapshot(self) -> list[ExperienceMemory]:
        """Best-effort read of every experience in the store.

        Lives behind a method so the future ``PgVectorEventFetcher``
        impl can plug in a SQL ``SELECT *`` without breaking the in-memory
        adapter's contract.
        """
        # InMemoryExperienceStore stores rows in ``_experiences`` (private
        # by convention but stable). Use ``getattr`` so this stays
        # tolerant if a future refactor renames the attribute.
        raw = getattr(self._store, "_experiences", None)
        if isinstance(raw, dict):
            return list(raw.values())
        return []


@runtime_checkable
class WindowedExperienceStore(Protocol):
    """An experience store that can return a time-window batch.

    Structural, not nominal, so :class:`PgVectorEventFetcher` works against
    ``PgVectorExperienceStore`` without importing it (keeping the asyncpg
    dependency out of the consolidation package) and against a test double.
    """

    async def list_window(
        self,
        *,
        since: datetime | None = ...,
        until: datetime | None = ...,
        project_id: UUID | None = ...,
        agent_code: str | None = ...,
        min_importance: float = ...,
        limit: int = ...,
    ) -> list[ExperienceMemory]: ...


class PgVectorEventFetcher(EventFetcher):
    """Reads the consolidation batch straight out of ``agent_experiences`` (MET-567).

    The production counterpart to :class:`InMemoryEventFetcher`. Filtering,
    ordering, and the ``limit`` cut all push down into SQL
    (``PgVectorExperienceStore.list_window``) instead of being applied to a
    Python snapshot, so a corpus larger than memory still consolidates.

    Pair it with the pgvector experience store; wiring the in-memory fetcher
    to a pgvector store is the exact defect this class exists to close (the
    private-dict snapshot is always empty there, so every pass synthesised
    nothing and ``memory.list_insights`` never returned a row).
    """

    def __init__(self, store: WindowedExperienceStore) -> None:
        self._store = store

    async def fetch(
        self,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        project_id: UUID | None = None,
        min_importance: float = DEFAULT_MIN_IMPORTANCE,
        limit: int = DEFAULT_FETCH_LIMIT,
    ) -> list[ExperienceMemory]:
        rows = await self._store.list_window(
            since=since,
            until=until,
            project_id=project_id,
            min_importance=min_importance,
            limit=limit,
        )
        logger.info(
            "consolidation_fetch_completed",
            backend="pgvector",
            returned=len(rows),
            project_id=str(project_id) if project_id else None,
            since=since.isoformat() if since else None,
            until=until.isoformat() if until else None,
            min_importance=min_importance,
        )
        return rows
