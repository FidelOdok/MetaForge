"""Dual-write ``InsightStore`` — fan out to a semantic + a structural backend.

The MET-454 writer spec is "Persist to Neo4j + pgvector". The two
backends serve different reads:

* **primary** (pgvector) — the semantic store, source of truth for
  reads (``list`` / ``get``). The narrative embedding lives here.
* **secondary** (Neo4j) — the structural store, write-only from this
  wrapper's perspective. Graph queries walk it directly.

``write`` fans out to both. The primary write must succeed (it's the
read path); a secondary write failure is logged and swallowed so a
transient Neo4j blip doesn't lose the insight from semantic search.
Reads always come from the primary so the wrapper never returns a
half-written record.
"""

from __future__ import annotations

from uuid import UUID

import structlog

from digital_twin.memory.consolidation.insight import Insight
from digital_twin.memory.consolidation.themes import ConsolidationTheme
from digital_twin.memory.consolidation.writer import InsightStore
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("digital_twin.memory.consolidation.dual_write")


class DualWriteInsightStore(InsightStore):
    """Write to two backends; read from the primary.

    The primary is authoritative for reads. The secondary is
    best-effort on write — a failure there is logged, counted, and
    swallowed rather than raised, so the semantic store stays the
    durable source of truth even if the graph store is flaky.
    """

    def __init__(self, primary: InsightStore, secondary: InsightStore) -> None:
        self._primary = primary
        self._secondary = secondary
        self._secondary_failures = 0

    @property
    def secondary_failures(self) -> int:
        """Count of secondary writes that failed since construction."""
        return self._secondary_failures

    async def write(self, insight: Insight) -> Insight:
        with tracer.start_as_current_span("dual_write_insight.write") as span:
            span.set_attribute("memory.insight_id", str(insight.id))
            # Primary write is on the critical path — let its exception
            # propagate so the caller knows the insight wasn't persisted
            # to the read store.
            persisted = await self._primary.write(insight)
            try:
                await self._secondary.write(insight)
            except Exception as exc:
                self._secondary_failures += 1
                span.set_attribute("memory.secondary_write_failed", True)
                span.record_exception(exc)
                logger.warning(
                    "dual_write_secondary_failed",
                    insight_id=str(insight.id),
                    error=str(exc),
                )
            return persisted

    async def list(
        self,
        *,
        theme: ConsolidationTheme | None = None,
        limit: int = 50,
    ) -> list[Insight]:
        return await self._primary.list(theme=theme, limit=limit)

    async def get(self, insight_id: UUID) -> Insight | None:
        return await self._primary.get(insight_id)
