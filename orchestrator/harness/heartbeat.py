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


# ---------------------------------------------------------------------------
# Periodic driver (MET-672)
# ---------------------------------------------------------------------------
#
# `HeartbeatMonitor` has had no production caller since MET-547 Phase 4 -- the
# docstring above describes "a cron/heartbeat job" that was never built, so
# abandoned runs sat non-terminal forever. Observed live: three
# `awaiting_approval` runs left behind by a client that was killed mid-turn,
# still listed as pending long afterwards.
#
# No `beat()` plumbing is required to make this useful: `sweep` ages a
# never-beaten run from its `created_at`, so the reaper works on today's runs
# as-is. A future run type that legitimately stays non-terminal longer than
# `stale_after` must either call `beat()` or raise the threshold.

DEFAULT_REAPER_INTERVAL_SECONDS = 300.0
"""How often to sweep. Cheap (an in-memory scan), so this is about how fast a
stale run should disappear, not about cost."""

DEFAULT_STALE_AFTER_SECONDS = 3600.0
"""Idle time before a non-terminal run is cancelled.

Must comfortably exceed the longest *legitimate* non-terminal lifetime, or the
reaper cancels live work. The relevant bound is the tool-approval wait
(`METAFORGE_CHAT_APPROVAL_TIMEOUT_SECONDS`, default 1800s), after which
`_await_approval` terminates the run itself -- so twice that is the floor."""


def reaper_interval_seconds() -> float:
    """``METAFORGE_RUN_REAPER_INTERVAL_SECONDS``; ``0`` disables the loop."""
    import os

    raw = os.environ.get("METAFORGE_RUN_REAPER_INTERVAL_SECONDS", "").strip()
    if not raw:
        return DEFAULT_REAPER_INTERVAL_SECONDS
    try:
        return max(0.0, float(raw))
    except ValueError:
        logger.warning("run_reaper_interval_invalid", value=raw)
        return DEFAULT_REAPER_INTERVAL_SECONDS


def stale_after_seconds() -> float:
    """``METAFORGE_RUN_STALE_AFTER_SECONDS``, floored above the approval wait.

    The floor is deliberate: a value below the approval timeout would let the
    reaper cancel a tool call while a human is still legitimately deciding on
    it, turning a working approval into a mysterious denial.
    """
    import os

    approval = 1800.0
    raw_approval = (os.environ.get("METAFORGE_CHAT_APPROVAL_TIMEOUT_SECONDS") or "").strip()
    try:
        approval = float(raw_approval) if raw_approval else approval
    except ValueError:
        pass
    floor = max(2.0 * approval, DEFAULT_STALE_AFTER_SECONDS)

    raw = os.environ.get("METAFORGE_RUN_STALE_AFTER_SECONDS", "").strip()
    if not raw:
        return floor
    try:
        requested = float(raw)
    except ValueError:
        logger.warning("run_stale_after_invalid", value=raw)
        return floor
    if requested < floor:
        logger.warning(
            "run_stale_after_below_floor",
            requested=requested,
            floor=floor,
            hint="a threshold under the approval wait would cancel live approvals",
        )
        return floor
    return requested


class RunReaper:
    """Sweeps a run store on an interval until stopped."""

    def __init__(
        self,
        store: InMemoryRunStore,
        monitor: HeartbeatMonitor | None = None,
        *,
        interval_seconds: float | None = None,
        stale_after: float | None = None,
        sleep: object = None,
    ) -> None:
        import asyncio

        self._store = store
        self._monitor = monitor or HeartbeatMonitor()
        self._interval = (
            interval_seconds if interval_seconds is not None else reaper_interval_seconds()
        )
        self._stale_after = stale_after if stale_after is not None else stale_after_seconds()
        self._sleep = sleep if sleep is not None else asyncio.sleep
        self._task: object = None
        self._sweeps = 0

    @property
    def running(self) -> bool:
        task = self._task
        return task is not None and not task.done()  # type: ignore[attr-defined]

    @property
    def sweeps(self) -> int:
        return self._sweeps

    def start(self) -> bool:
        """Launch the loop. Returns False when disabled or already running."""
        import asyncio

        if self._interval <= 0:
            logger.info("run_reaper_disabled", interval_seconds=self._interval)
            return False
        if self.running:
            return False
        self._task = asyncio.create_task(self._loop(), name="run-reaper")
        logger.info(
            "run_reaper_started",
            interval_seconds=self._interval,
            stale_after=self._stale_after,
        )
        return True

    async def stop(self) -> None:
        """Cancel the loop and wait for it to unwind (idempotent)."""
        import asyncio

        task = self._task
        self._task = None
        if task is None or task.done():  # type: ignore[attr-defined]
            return
        task.cancel()  # type: ignore[attr-defined]
        try:
            await task  # type: ignore[misc]
        except (asyncio.CancelledError, Exception):  # noqa: BLE001 — shutdown path
            pass
        logger.info("run_reaper_stopped", sweeps=self._sweeps)

    def sweep_once(self) -> SweepReport:
        """One sweep. Never raises — a reaper must not take the gateway down."""
        self._sweeps += 1
        try:
            report = self._monitor.sweep(self._store, stale_after=self._stale_after)
        except Exception as exc:  # noqa: BLE001
            logger.warning("run_reaper_sweep_failed", error=str(exc))
            return SweepReport()
        if report.abandoned:
            logger.info(
                "run_reaper_swept",
                checked=report.checked,
                abandoned=len(report.abandoned),
            )
        return report

    async def _loop(self) -> None:
        while True:
            await self._sleep(self._interval)  # type: ignore[operator]
            self.sweep_once()
