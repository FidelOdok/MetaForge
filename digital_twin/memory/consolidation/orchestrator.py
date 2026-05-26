"""End-to-end consolidation orchestrator.

Wires the 4 deterministic stages of the consolidation pipeline:
``fetcher → grouper → synthesizer → validator → writer``. The
S3 ``EventArchiver`` and the Temporal workflow wrapper land in
follow-up commits — keeping them separate means the in-process
orchestrator stays trivially unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

import structlog

from digital_twin.memory.consolidation.fetcher import (
    DEFAULT_FETCH_LIMIT,
    DEFAULT_MIN_IMPORTANCE,
    EventFetcher,
)
from digital_twin.memory.consolidation.grouper import EventGrouper
from digital_twin.memory.consolidation.insight import Insight
from digital_twin.memory.consolidation.synthesizer import InsightSynthesizer
from digital_twin.memory.consolidation.themes import ConsolidationTheme
from digital_twin.memory.consolidation.validator import InsightValidator
from digital_twin.memory.consolidation.writer import SemanticMemoryWriter
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("digital_twin.memory.consolidation.orchestrator")


@dataclass(frozen=True)
class ConsolidationReport:
    """Audit trail returned by ``ConsolidationOrchestrator.run``.

    Lets the caller (or the Temporal workflow) record what happened on
    each pass without scraping logs.
    """

    fetched_count: int = 0
    group_count: int = 0
    synthesized_count: int = 0
    accepted_count: int = 0
    rejected_count: int = 0
    written_by_theme: dict[ConsolidationTheme, int] = field(default_factory=dict)
    rejected_reasons: list[str] = field(default_factory=list)
    insights: list[Insight] = field(default_factory=list)


class ConsolidationOrchestrator:
    """Drive one consolidation pass: fetch → group → synth → validate → write."""

    def __init__(
        self,
        *,
        fetcher: EventFetcher,
        grouper: EventGrouper,
        synthesizer: InsightSynthesizer,
        validator: InsightValidator,
        writer: SemanticMemoryWriter,
    ) -> None:
        self._fetcher = fetcher
        self._grouper = grouper
        self._synthesizer = synthesizer
        self._validator = validator
        self._writer = writer

    async def run(
        self,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        project_id: UUID | None = None,
        min_importance: float = DEFAULT_MIN_IMPORTANCE,
        fetch_limit: int = DEFAULT_FETCH_LIMIT,
    ) -> ConsolidationReport:
        """Execute a single end-to-end consolidation pass."""
        with tracer.start_as_current_span("consolidation.orchestrator.run") as span:
            self._writer.reset_counters()

            experiences = await self._fetcher.fetch(
                since=since,
                until=until,
                project_id=project_id,
                min_importance=min_importance,
                limit=fetch_limit,
            )
            span.set_attribute("memory.fetched_count", len(experiences))
            if not experiences:
                return ConsolidationReport()

            groups = self._grouper.group(experiences)
            span.set_attribute("memory.group_count", len(groups))

            synthesized: list[Insight] = []
            accepted: list[Insight] = []
            rejected: list[str] = []

            for group in groups:
                insight = await self._synthesizer.synthesize(group)
                if insight is None:
                    rejected.append(
                        f"theme={group.theme.value} reason=synthesis_failed"
                    )
                    continue
                synthesized.append(insight)
                verdict = self._validator.validate(insight)
                if not verdict.accepted:
                    rejected.append(
                        f"theme={group.theme.value} reason={verdict.reason}"
                    )
                    continue
                await self._writer.write(insight)
                accepted.append(insight)

            written = self._writer.written_by_theme()
            report = ConsolidationReport(
                fetched_count=len(experiences),
                group_count=len(groups),
                synthesized_count=len(synthesized),
                accepted_count=len(accepted),
                rejected_count=len(rejected),
                written_by_theme=written,
                rejected_reasons=rejected,
                insights=accepted,
            )
            span.set_attribute("memory.accepted_count", report.accepted_count)
            span.set_attribute("memory.rejected_count", report.rejected_count)
            logger.info(
                "consolidation_pass_completed",
                fetched=report.fetched_count,
                groups=report.group_count,
                synthesized=report.synthesized_count,
                accepted=report.accepted_count,
                rejected=report.rejected_count,
                project_id=str(project_id) if project_id else None,
            )
            return report
