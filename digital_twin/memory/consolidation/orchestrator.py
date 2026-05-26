"""End-to-end consolidation orchestrator.

Wires the 4 deterministic stages of the consolidation pipeline:
``fetcher → grouper → synthesizer → validator → writer``. The
S3 ``EventArchiver`` and the Temporal workflow wrapper land in
follow-up commits — keeping them separate means the in-process
orchestrator stays trivially unit-testable.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

import structlog

from digital_twin.memory.consolidation.contradiction_detector import ContradictionDetector
from digital_twin.memory.consolidation.decay import ConfidenceDecay
from digital_twin.memory.consolidation.fetcher import (
    DEFAULT_FETCH_LIMIT,
    DEFAULT_MIN_IMPORTANCE,
    EventFetcher,
)
from digital_twin.memory.consolidation.grouper import EventGrouper
from digital_twin.memory.consolidation.insight import Insight, InsightStatus
from digital_twin.memory.consolidation.modes import (
    ConsolidationMode,
    ConsolidationRunRequest,
)
from digital_twin.memory.consolidation.synthesizer import InsightSynthesizer
from digital_twin.memory.consolidation.themes import ConsolidationTheme
from digital_twin.memory.consolidation.validator import InsightValidator
from digital_twin.memory.consolidation.writer import (
    InsightStore,
    SemanticMemoryWriter,
)
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
    mode: ConsolidationMode = ConsolidationMode.BACKGROUND
    revalidated_count: int = 0
    """JANITOR mode only — how many existing insights were re-checked."""
    newly_failed_count: int = 0
    """JANITOR mode only — how many existing insights failed re-validation."""
    marked_stale_count: int = 0
    """JANITOR mode only — how many insights were written back as STALE_WARN."""
    contradictions: list[str] = field(default_factory=list)
    """Human-readable records of synthesized insights that conflict with the
    existing corpus (populated only when a ContradictionDetector is wired)."""


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
        insight_store: InsightStore | None = None,
        decay: ConfidenceDecay | None = None,
        janitor_marks_stale: bool = False,
        contradiction_detector: ContradictionDetector | None = None,
        collector: Any = None,
    ) -> None:
        self._fetcher = fetcher
        self._grouper = grouper
        self._synthesizer = synthesizer
        self._validator = validator
        self._writer = writer
        # Optional observability.MetricsCollector. Duck-typed (Any) to
        # keep this module free of a hard observability import; when
        # supplied, run_request emits the MET-454/455 consolidation
        # metrics (pass duration + accepted/rejected/contradiction/
        # stale-marked counters).
        self._collector = collector
        # JANITOR mode needs to read previously-persisted insights; the
        # writer exposes its own backing store via the public ``store``
        # attribute when supplied. Callers that don't run janitor passes
        # can leave this None.
        self._insight_store = insight_store
        # MET-455: when supplied, JANITOR applies time-decay to each
        # insight's confidence before re-validating, so an insight that
        # has simply aged past the validator's floor gets flagged as
        # stale ("active forgetting"). When None, JANITOR re-validates
        # against the raw stored confidence (drift detection only).
        self._decay = decay
        # MET-455: when True, JANITOR persists status=STALE_WARN back to
        # the store for insights that fail re-validation (durable
        # flagging). When False (default), JANITOR is report-only and
        # never mutates the store.
        self._janitor_marks_stale = janitor_marks_stale
        # MET-455: when wired (alongside an insight_store), the BACKGROUND
        # pass checks each newly-synthesized insight against the existing
        # same-theme corpus and records conflicts in the report. The new
        # insight is still written (newest lesson wins); the contradiction
        # is surfaced for review rather than silently dropped.
        self._contradiction_detector = contradiction_detector

    async def run(
        self,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        project_id: UUID | None = None,
        min_importance: float = DEFAULT_MIN_IMPORTANCE,
        fetch_limit: int = DEFAULT_FETCH_LIMIT,
        mode: ConsolidationMode = ConsolidationMode.BACKGROUND,
    ) -> ConsolidationReport:
        """Execute a single end-to-end consolidation pass."""
        request = ConsolidationRunRequest(
            mode=mode,
            since=since,
            until=until,
            project_id=project_id,
            theme=None,
            min_importance=min_importance if min_importance != DEFAULT_MIN_IMPORTANCE else None,
            fetch_limit=fetch_limit if fetch_limit != DEFAULT_FETCH_LIMIT else None,
        )
        return await self.run_request(request)

    async def run_request(self, request: ConsolidationRunRequest) -> ConsolidationReport:
        """Execute a single pass driven by a ``ConsolidationRunRequest``.

        Preferred entry point for the Temporal workflow and other
        callers that already build a structured request. The legacy
        ``run(**kwargs)`` form delegates here.
        """
        with tracer.start_as_current_span("consolidation.orchestrator.run") as span:
            span.set_attribute("memory.mode", request.mode.value)
            started = time.monotonic()
            if request.mode == ConsolidationMode.JANITOR:
                report = await self._run_janitor(request)
            else:
                report = await self._run_synthesis_pass(request, span)
            if self._collector is not None:
                self._collector.record_consolidation_pass(
                    request.mode.value,
                    time.monotonic() - started,
                    accepted=report.accepted_count,
                    rejected=report.rejected_count,
                    contradictions=len(report.contradictions),
                    stale_marked=report.marked_stale_count,
                )
            return report

    async def _run_synthesis_pass(
        self,
        request: ConsolidationRunRequest,
        span: Any,
    ) -> ConsolidationReport:
        self._writer.reset_counters()

        floor = request.effective_min_importance
        if floor is None:
            floor = DEFAULT_MIN_IMPORTANCE
        fetch_limit = (
            request.fetch_limit if request.fetch_limit is not None else DEFAULT_FETCH_LIMIT
        )

        experiences = await self._fetcher.fetch(
            since=request.since,
            until=request.until,
            project_id=request.project_id,
            min_importance=floor,
            limit=fetch_limit,
        )
        span.set_attribute("memory.fetched_count", len(experiences))
        if not experiences:
            return ConsolidationReport(mode=request.mode)

        groups = self._grouper.group(experiences)
        # PROACTIVE narrows to one theme when supplied — drop groups
        # that aren't the requested theme so the LLM budget stays
        # focused on the upstream signal.
        if request.mode == ConsolidationMode.PROACTIVE and request.theme is not None:
            groups = [g for g in groups if g.theme == request.theme]
        span.set_attribute("memory.group_count", len(groups))

        synthesized: list[Insight] = []
        accepted: list[Insight] = []
        rejected: list[str] = []
        contradictions: list[str] = []

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
            # MET-455: check the candidate against the existing corpus
            # BEFORE writing it, so it doesn't compare against itself.
            await self._record_contradictions(insight, contradictions)
            await self._writer.write(insight)
            accepted.append(insight)

        written = self._writer.written_by_theme()
        report = ConsolidationReport(
            mode=request.mode,
            fetched_count=len(experiences),
            group_count=len(groups),
            synthesized_count=len(synthesized),
            accepted_count=len(accepted),
            rejected_count=len(rejected),
            written_by_theme=written,
            rejected_reasons=rejected,
            contradictions=contradictions,
            insights=accepted,
        )
        span.set_attribute("memory.accepted_count", report.accepted_count)
        span.set_attribute("memory.rejected_count", report.rejected_count)
        logger.info(
            "consolidation_pass_completed",
            mode=request.mode.value,
            fetched=report.fetched_count,
            groups=report.group_count,
            synthesized=report.synthesized_count,
            accepted=report.accepted_count,
            rejected=report.rejected_count,
            project_id=str(request.project_id) if request.project_id else None,
        )
        return report

    async def _record_contradictions(
        self,
        candidate: Insight,
        sink: list[str],
    ) -> None:
        """Detect + record conflicts between ``candidate`` and the stored corpus.

        No-op unless both a contradiction detector and an insight store
        are wired. Best-effort: a detector failure is logged inside the
        detector (fail-open) and never blocks the write.
        """
        if self._contradiction_detector is None or self._insight_store is None:
            return
        existing = await self._insight_store.list(theme=candidate.theme, limit=100)
        result = await self._contradiction_detector.detect(candidate, existing)
        if result.contradicts:
            conflict_ids = ", ".join(str(cid) for cid in result.conflicting_insight_ids)
            sink.append(
                f"candidate={candidate.id} theme={candidate.theme.value}"
                f" conflicts_with=[{conflict_ids}] :: {result.explanation}"
            )
            logger.info(
                "consolidation_contradiction_detected",
                candidate_id=str(candidate.id),
                theme=candidate.theme.value,
                conflict_count=len(result.conflicting_insight_ids),
            )

    async def _run_janitor(
        self,
        request: ConsolidationRunRequest,
    ) -> ConsolidationReport:
        """Re-validate previously-persisted insights; report newly-failed ones.

        JANITOR mode never synthesizes new insights — it loops over the
        existing store and checks each one against the current
        ``InsightValidator`` thresholds. Useful for detecting drift when
        the validator's confidence floor tightens or hallucination
        patterns expand. Newly-failed insights are reported but **not**
        deleted; that's an engineer decision.
        """
        if self._insight_store is None:
            logger.warning(
                "consolidation_janitor_skipped",
                reason="no_insight_store_wired",
            )
            return ConsolidationReport(mode=request.mode)

        existing = await self._insight_store.list(theme=request.theme, limit=10_000)
        rejected: list[str] = []
        newly_failed = 0
        marked_stale = 0
        for insight in existing:
            # MET-455: apply confidence decay (if configured) before
            # re-validating so aged insights surface as stale.
            candidate = (
                self._decay.with_decayed_confidence(insight)
                if self._decay is not None
                else insight
            )
            verdict = self._validator.validate(candidate)
            if not verdict.accepted:
                newly_failed += 1
                rejected.append(
                    f"insight_id={insight.id} theme={insight.theme.value}"
                    f" reason={verdict.reason}"
                )
                # MET-455: durably flag the insight when configured to do
                # so. Only write back when the status actually changes —
                # an already-STALE_WARN insight needs no re-write.
                if (
                    self._janitor_marks_stale
                    and insight.status is not InsightStatus.STALE_WARN
                ):
                    try:
                        await self._insight_store.write(
                            insight.model_copy(
                                update={"status": InsightStatus.STALE_WARN}
                            )
                        )
                        marked_stale += 1
                    except Exception as exc:  # pragma: no cover — best effort
                        logger.warning(
                            "consolidation_janitor_mark_failed",
                            insight_id=str(insight.id),
                            error=str(exc),
                        )
        report = ConsolidationReport(
            mode=request.mode,
            revalidated_count=len(existing),
            newly_failed_count=newly_failed,
            marked_stale_count=marked_stale,
            rejected_count=newly_failed,
            rejected_reasons=rejected,
        )
        logger.info(
            "consolidation_janitor_completed",
            revalidated=report.revalidated_count,
            newly_failed=report.newly_failed_count,
            marked_stale=report.marked_stale_count,
            decay_applied=self._decay is not None,
            project_id=str(request.project_id) if request.project_id else None,
        )
        return report
