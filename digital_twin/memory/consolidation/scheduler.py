"""Periodic driver for the consolidation pipeline (MET-567).

The pipeline itself (``ConsolidationOrchestrator``) has been complete since
MET-454/455, and a Temporal ``ConsolidationWorkflow`` wraps it — but nothing
ever *triggered* either one. Insights only appeared if someone hit the
on-demand REST/CLI path by hand, so ``memory.list_insights`` was empty in
every real deployment no matter how much the experience store filled up.

This module closes that gap with the smallest dependency-free mechanism that
works wherever the gateway runs: an asyncio task that runs one BACKGROUND
pass per interval. It is deliberately *not* Temporal — the gateway does not
run a Temporal worker today (``orchestrator/temporal_worker.py`` registers
the agent workflows only), so a Temporal schedule would have kept
consolidation dormant. When a worker does come up, point it at
``ConsolidationWorkflow`` and set the interval to 0 to hand the job over.

Every pass is best-effort: a failure is logged and the loop keeps its
cadence, because a broken consolidation pass must never take the gateway
with it.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

from digital_twin.memory.consolidation.fetcher import DEFAULT_FETCH_LIMIT
from digital_twin.memory.consolidation.modes import ConsolidationMode
from digital_twin.memory.consolidation.orchestrator import (
    ConsolidationOrchestrator,
    ConsolidationReport,
)
from digital_twin.memory.consolidation.workflow import DEFAULT_INTERVAL_SECONDS
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("digital_twin.memory.consolidation.scheduler")

# The cadence contract is the MET-454 spec's 30 minutes, already pinned as
# ``DEFAULT_INTERVAL_SECONDS`` for the Temporal workflow — reused here rather
# than re-declared, so the two drivers of the same pipeline cannot drift apart.
# Override per-deployment with ``METAFORGE_CONSOLIDATION_INTERVAL_SECONDS``;
# ``0`` disables the loop.

_LOOKBACK_FACTOR = 2.0
"""Each pass looks back twice the interval so an experience written moments
before a pass boundary is still inside the *next* pass's window. Overlap is
harmless — synthesis is idempotent in effect (the validator gates duplicates
and JANITOR re-checks the corpus), whereas a gap loses the lesson for good."""


def interval_seconds_from_env() -> float:
    """Configured pass interval; ``0.0`` (or a bad value) means disabled."""
    raw = os.environ.get("METAFORGE_CONSOLIDATION_INTERVAL_SECONDS", "").strip()
    if not raw:
        return DEFAULT_INTERVAL_SECONDS
    try:
        value = float(raw)
    except ValueError:
        logger.warning("consolidation_interval_invalid", value=raw)
        return DEFAULT_INTERVAL_SECONDS
    return max(0.0, value)


class ConsolidationScheduler:
    """Runs one consolidation pass per ``interval_seconds`` until stopped."""

    def __init__(
        self,
        orchestrator: ConsolidationOrchestrator,
        *,
        interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
        mode: ConsolidationMode = ConsolidationMode.BACKGROUND,
        sleep: Any = asyncio.sleep,
    ) -> None:
        self._orchestrator = orchestrator
        self._interval = interval_seconds
        self._mode = mode
        self._sleep = sleep
        self._task: asyncio.Task[None] | None = None
        self._passes = 0

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def passes(self) -> int:
        """Completed passes — attempted, whether or not they synthesised."""
        return self._passes

    def start(self) -> bool:
        """Launch the background loop. Returns False when disabled/already up."""
        if self._interval <= 0:
            logger.info("consolidation_scheduler_disabled", interval_seconds=self._interval)
            return False
        if self.running:
            return False
        self._task = asyncio.create_task(self._loop(), name="consolidation-scheduler")
        logger.info("consolidation_scheduler_started", interval_seconds=self._interval)
        return True

    async def stop(self) -> None:
        """Cancel the loop and wait for it to unwind (idempotent)."""
        task = self._task
        self._task = None
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001 — shutdown path
            pass
        logger.info("consolidation_scheduler_stopped", passes=self._passes)

    async def run_once(self) -> ConsolidationReport | None:
        """Run a single pass. Returns ``None`` when the pass raised."""
        with tracer.start_as_current_span("consolidation.scheduled_pass") as span:
            self._passes += 1
            try:
                report = await self._orchestrator.run(
                    mode=self._mode,
                    # ``since=None`` would re-consolidate the entire corpus on
                    # every pass — the LLM bill grows without bound and old
                    # experiences get re-synthesised forever. The window keeps
                    # each pass proportional to what actually happened.
                    since=self._window_start(),
                    fetch_limit=DEFAULT_FETCH_LIMIT,
                )
            except Exception as exc:  # noqa: BLE001 — never kill the loop
                span.record_exception(exc)
                logger.warning("consolidation_pass_failed", error=str(exc))
                return None
            span.set_attribute("consolidation.accepted", report.accepted_count)
            logger.info(
                "consolidation_pass_completed",
                fetched=report.fetched_count,
                groups=report.group_count,
                accepted=report.accepted_count,
                rejected=report.rejected_count,
            )
            return report

    def _window_start(self) -> datetime:
        return datetime.now(UTC) - timedelta(seconds=self._interval * _LOOKBACK_FACTOR)

    async def _loop(self) -> None:
        # Sleep first: at boot the window is empty by definition, and a pass
        # during startup competes with the rest of lifespan wiring.
        while True:
            await self._sleep(self._interval)
            await self.run_once()
