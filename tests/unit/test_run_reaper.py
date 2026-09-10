"""Periodic reaping of abandoned runs (MET-672).

``HeartbeatMonitor`` has existed since MET-547 Phase 4 with **no production
caller** — its own docstring describes "a cron/heartbeat job periodically
calls sweep", and that job was never built, so an abandoned run sat
non-terminal forever. Observed live: three ``awaiting_approval`` runs left
behind by a client killed mid-turn, still listed as pending long afterwards.

The threshold tests matter most. A reaper whose window is too short is worse
than no reaper: it cancels a tool call while a human is still legitimately
deciding on it, turning a working approval into a mysterious denial.
"""

from __future__ import annotations

import asyncio

import pytest

from orchestrator.harness.heartbeat import (
    DEFAULT_REAPER_INTERVAL_SECONDS,
    DEFAULT_STALE_AFTER_SECONDS,
    HeartbeatMonitor,
    RunReaper,
    reaper_interval_seconds,
    stale_after_seconds,
)
from orchestrator.harness.runs import InMemoryRunStore


class Clock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _store_with_a_pending_run(clock: Clock) -> tuple[InMemoryRunStore, str]:
    store = InMemoryRunStore(clock=clock)
    run = store.create({"tool": "twin.commit_geometry"})
    store.start(run.id)
    store.request_approval(run.id, reason="approval required")
    return store, run.id


class TestSweeping:
    def test_an_abandoned_run_is_cancelled(self):
        # The live case: a client dies mid-turn and its approval run is
        # orphaned in awaiting_approval.
        clock = Clock()
        store, run_id = _store_with_a_pending_run(clock)
        reaper = RunReaper(
            store, HeartbeatMonitor(clock=clock), interval_seconds=1, stale_after=100
        )

        clock.now += 101
        report = reaper.sweep_once()

        assert report.abandoned == [run_id]
        assert store.get(run_id).is_terminal

    def test_a_fresh_run_is_left_alone(self):
        clock = Clock()
        store, run_id = _store_with_a_pending_run(clock)
        reaper = RunReaper(
            store, HeartbeatMonitor(clock=clock), interval_seconds=1, stale_after=100
        )

        clock.now += 50
        report = reaper.sweep_once()

        assert report.abandoned == []
        assert not store.get(run_id).is_terminal

    def test_a_beaten_run_survives_past_its_creation_age(self):
        # No beat() plumbing exists today, but the contract must hold for the
        # long-running run types that will need it.
        clock = Clock()
        store, run_id = _store_with_a_pending_run(clock)
        monitor = HeartbeatMonitor(clock=clock)
        reaper = RunReaper(store, monitor, interval_seconds=1, stale_after=100)

        clock.now += 90
        monitor.beat(run_id)
        clock.now += 90  # 180s old, but only 90s since the beat

        assert reaper.sweep_once().abandoned == []

    def test_a_sweep_failure_never_propagates(self):
        class _Broken(HeartbeatMonitor):
            def sweep(self, store, *, stale_after):  # type: ignore[override]
                raise RuntimeError("store exploded")

        reaper = RunReaper(InMemoryRunStore(), _Broken(), interval_seconds=1, stale_after=1)

        report = reaper.sweep_once()  # must not raise

        assert report.abandoned == []
        assert reaper.sweeps == 1


class TestThresholds:
    def test_interval_defaults_and_disable_switch(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("METAFORGE_RUN_REAPER_INTERVAL_SECONDS", raising=False)
        assert reaper_interval_seconds() == DEFAULT_REAPER_INTERVAL_SECONDS

        monkeypatch.setenv("METAFORGE_RUN_REAPER_INTERVAL_SECONDS", "60")
        assert reaper_interval_seconds() == 60.0

        monkeypatch.setenv("METAFORGE_RUN_REAPER_INTERVAL_SECONDS", "0")
        assert reaper_interval_seconds() == 0.0

        monkeypatch.setenv("METAFORGE_RUN_REAPER_INTERVAL_SECONDS", "nonsense")
        assert reaper_interval_seconds() == DEFAULT_REAPER_INTERVAL_SECONDS

    def test_stale_after_never_drops_below_the_approval_wait(self, monkeypatch: pytest.MonkeyPatch):
        # This is the dangerous direction: a 60s window would cancel a tool
        # call a human is still deciding on, and the turn would report a denial
        # nobody issued.
        monkeypatch.setenv("METAFORGE_CHAT_APPROVAL_TIMEOUT_SECONDS", "1800")
        monkeypatch.setenv("METAFORGE_RUN_STALE_AFTER_SECONDS", "60")

        assert stale_after_seconds() >= 3600.0

    def test_stale_after_tracks_a_raised_approval_timeout(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("METAFORGE_CHAT_APPROVAL_TIMEOUT_SECONDS", "7200")
        monkeypatch.delenv("METAFORGE_RUN_STALE_AFTER_SECONDS", raising=False)

        # Twice the approval wait, not the static default.
        assert stale_after_seconds() == 14400.0

    def test_an_explicit_larger_value_is_honoured(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("METAFORGE_CHAT_APPROVAL_TIMEOUT_SECONDS", "1800")
        monkeypatch.setenv("METAFORGE_RUN_STALE_AFTER_SECONDS", "86400")

        assert stale_after_seconds() == 86400.0

    def test_defaults_are_ordered_sanely(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("METAFORGE_CHAT_APPROVAL_TIMEOUT_SECONDS", raising=False)
        monkeypatch.delenv("METAFORGE_RUN_STALE_AFTER_SECONDS", raising=False)

        # Sweep far more often than the window, or a stale run lingers a whole
        # extra window before anyone notices.
        assert reaper_interval_seconds() < stale_after_seconds()
        assert stale_after_seconds() >= DEFAULT_STALE_AFTER_SECONDS


class TestLoop:
    @pytest.mark.asyncio
    async def test_the_loop_sweeps_then_stops(self):
        clock = Clock()
        store, run_id = _store_with_a_pending_run(clock)
        ticks = asyncio.Event()

        async def fake_sleep(_seconds: float) -> None:
            clock.now += 200
            ticks.set()
            await asyncio.sleep(0)

        reaper = RunReaper(
            store,
            HeartbeatMonitor(clock=clock),
            interval_seconds=1,
            stale_after=100,
            sleep=fake_sleep,
        )
        assert reaper.start() is True
        await ticks.wait()
        await asyncio.sleep(0)
        await reaper.stop()

        assert reaper.running is False
        assert store.get(run_id).is_terminal

    def test_start_is_a_no_op_when_disabled(self):
        reaper = RunReaper(InMemoryRunStore(), interval_seconds=0)

        assert reaper.start() is False
        assert reaper.running is False

    @pytest.mark.asyncio
    async def test_stop_is_safe_before_start_and_idempotent(self):
        reaper = RunReaper(InMemoryRunStore(), interval_seconds=1)

        await reaper.stop()
        await reaper.stop()
