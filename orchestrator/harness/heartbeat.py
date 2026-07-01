"""Heartbeat re-validation / stale-run reaping (MET-547, Phase 4).

A long-lived run can be abandoned — the client goes away mid-run and it sits
non-terminal forever. A cron/heartbeat job periodically calls
:meth:`HeartbeatMonitor.sweep`, which cancels any non-terminal run that hasn't
been "beaten" within ``stale_after`` seconds. Runs report liveness via
:meth:`beat`; a run that never beat falls back to its ``created_at`` so it
still ages out.

Deterministic and stdlib-only: the clock is injected so tests control time.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

import structlog

from orchestrator.harness.runs import InMemoryRunStore, InvalidTransition

logger = structlog.get_logger(__name__)


@dataclass
class SweepReport:
    """Result of one sweep."""

    checked: int = 0
    abandoned: list[str] = field(default_factory=list)


class HeartbeatMonitor:
    """Track per-run liveness and reap abandoned runs."""

    def __init__(self, *, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._last: dict[str, float] = {}

    def beat(self, run_id: str) -> None:
        """Record that ``run_id`` is still alive right now."""
        self._last[run_id] = self._clock()

    def last_seen(self, run_id: str) -> float | None:
        return self._last.get(run_id)

    def sweep(self, store: InMemoryRunStore, *, stale_after: float) -> SweepReport:
        """Cancel every non-terminal run idle longer than ``stale_after``."""
        now = self._clock()
        report = SweepReport()
        for run in store.list():
            if run.is_terminal:
                continue
            report.checked += 1
            # Never-beaten runs age from their creation time.
            seen = self._last.get(run.id, run.created_at)
            if now - seen > stale_after:
                try:
                    store.cancel(run.id)
                except InvalidTransition:
                    continue
                self._last.pop(run.id, None)
                report.abandoned.append(run.id)
                logger.warning("run_abandoned", run_id=run.id, idle=now - seen)
        return report
