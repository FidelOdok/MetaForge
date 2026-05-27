"""Stage 5 of the consolidation pipeline — persist validated insights.

Real production wiring uses both Neo4j (structured edges from insight
to component / decision nodes) and pgvector (embedded narrative for
semantic search). This module holds the storage Protocol + an
in-memory adapter; concrete Neo4j and pgvector adapters land in a
follow-up alongside the matching schema migration.

The Protocol design keeps the orchestrator backend-agnostic. A future
``DualWriteInsightStore`` can wrap both backends so the orchestrator
still sees a single ``write`` call.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from uuid import UUID

import structlog

from digital_twin.memory.consolidation.insight import Insight
from digital_twin.memory.consolidation.themes import ConsolidationTheme
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("digital_twin.memory.consolidation.writer")


class InsightStore(ABC):
    """Storage backend for validated ``Insight`` records."""

    @abstractmethod
    async def write(self, insight: Insight) -> Insight:
        """Persist an insight; return it (possibly with backend-assigned fields)."""

    @abstractmethod
    async def list(
        self,
        *,
        theme: ConsolidationTheme | None = None,
        limit: int = 50,
    ) -> list[Insight]:
        """Return previously-persisted insights, newest first."""

    @abstractmethod
    async def get(self, insight_id: UUID) -> Insight | None:
        """Look up an insight by id."""


class InMemoryInsightStore(InsightStore):
    """Dict-backed store for development / unit tests."""

    def __init__(self) -> None:
        self._insights: dict[UUID, Insight] = {}

    async def write(self, insight: Insight) -> Insight:
        self._insights[insight.id] = insight
        logger.info(
            "consolidation_insight_stored",
            insight_id=str(insight.id),
            theme=insight.theme.value,
            confidence=insight.confidence,
        )
        return insight

    async def list(
        self,
        *,
        theme: ConsolidationTheme | None = None,
        limit: int = 50,
    ) -> list[Insight]:
        candidates: Iterable[Insight] = self._insights.values()
        if theme is not None:
            candidates = (i for i in candidates if i.theme == theme)
        ordered = sorted(candidates, key=lambda i: i.synthesized_at, reverse=True)
        return ordered[: max(0, limit)]

    async def get(self, insight_id: UUID) -> Insight | None:
        return self._insights.get(insight_id)


class SemanticMemoryWriter:
    """Wraps an ``InsightStore`` with tracing + structured logging.

    Kept as its own class (rather than callers using the store
    directly) so a future dual-write adapter can intercept the
    ``write`` boundary without changing every callsite. The wrapper
    also keeps a running count of writes per theme, which the
    orchestrator exposes in its `ConsolidationReport`.
    """

    def __init__(self, store: InsightStore) -> None:
        self._store = store
        self._counters: dict[ConsolidationTheme, int] = {}

    async def write(self, insight: Insight) -> Insight:
        with tracer.start_as_current_span("consolidation.writer.write") as span:
            span.set_attribute("memory.insight_id", str(insight.id))
            span.set_attribute("memory.theme", insight.theme.value)
            persisted = await self._store.write(insight)
            self._counters[insight.theme] = self._counters.get(insight.theme, 0) + 1
            return persisted

    def written_by_theme(self) -> dict[ConsolidationTheme, int]:
        """Snapshot of per-theme write counts since construction."""
        return dict(self._counters)

    def reset_counters(self) -> None:
        """Clear the per-theme counters (call at the start of a pass)."""
        self._counters.clear()
