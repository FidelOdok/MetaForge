"""Unit tests for heartbeat stale-run reaping (MET-547, Phase 4)."""

from __future__ import annotations

from orchestrator.harness.heartbeat import HeartbeatMonitor
from orchestrator.harness.runs import InMemoryRunStore, RunStatus


class Clock:
    """Mutable clock shared by the store and the monitor."""

    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def _setup() -> tuple[Clock, InMemoryRunStore, HeartbeatMonitor]:
    clock = Clock()
    store = InMemoryRunStore(clock=clock)
    monitor = HeartbeatMonitor(clock=clock)
    return clock, store, monitor


def test_beat_records_last_seen() -> None:
    clock, _, monitor = _setup()
    monitor.beat("r1")
    assert monitor.last_seen("r1") == 100.0
    assert monitor.last_seen("r2") is None


def test_sweep_cancels_abandoned_run() -> None:
    clock, store, monitor = _setup()
    store.create({}, run_id="r1")  # created at t=100, never beats
    store.start("r1")
    clock.t = 100.0 + 400.0  # 400s later
    report = monitor.sweep(store, stale_after=300.0)
    assert report.abandoned == ["r1"]
    assert store.get("r1").status is RunStatus.CANCELED


def test_sweep_keeps_fresh_run() -> None:
    clock, store, monitor = _setup()
    store.create({}, run_id="r1")
    store.start("r1")
    clock.t = 500.0
    monitor.beat("r1")  # fresh heartbeat at t=500
    clock.t = 600.0  # only 100s since beat
    report = monitor.sweep(store, stale_after=300.0)
    assert report.abandoned == []
    assert store.get("r1").status is RunStatus.RUNNING


def test_sweep_ignores_terminal_runs() -> None:
    clock, store, monitor = _setup()
    store.create({}, run_id="r1")
    store.start("r1")
    store.complete("r1")
    clock.t = 100_000.0  # far in the future
    report = monitor.sweep(store, stale_after=1.0)
    assert report.checked == 0  # terminal run not even counted
    assert report.abandoned == []


def test_sweep_reports_checked_count() -> None:
    clock, store, monitor = _setup()
    store.create({}, run_id="r1")
    store.create({}, run_id="r2")
    clock.t = 100.0  # same instant, nothing stale yet
    report = monitor.sweep(store, stale_after=300.0)
    assert report.checked == 2
    assert report.abandoned == []
