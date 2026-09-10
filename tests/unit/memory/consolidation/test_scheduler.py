"""Unit tests for ``ConsolidationScheduler`` (MET-567)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from digital_twin.memory.consolidation.modes import ConsolidationMode
from digital_twin.memory.consolidation.orchestrator import ConsolidationReport
from digital_twin.memory.consolidation.scheduler import (
    DEFAULT_INTERVAL_SECONDS,
    ConsolidationScheduler,
    interval_seconds_from_env,
)


class FakeOrchestrator:
    def __init__(self, *, raises: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self._raises = raises

    async def run(self, **kwargs: Any) -> ConsolidationReport:
        self.calls.append(kwargs)
        if self._raises:
            raise RuntimeError("open router is down")
        return ConsolidationReport(fetched_count=3, group_count=1, accepted_count=1)


@pytest.mark.asyncio
async def test_run_once_runs_a_background_pass_over_a_bounded_window():
    orch = FakeOrchestrator()
    scheduler = ConsolidationScheduler(orch, interval_seconds=600.0)  # type: ignore[arg-type]

    report = await scheduler.run_once()

    assert report is not None
    assert report.accepted_count == 1
    call = orch.calls[0]
    assert call["mode"] is ConsolidationMode.BACKGROUND
    # A window, not the whole corpus: an unbounded `since` would re-synthesise
    # every experience ever recorded on every pass.
    assert call["since"] is not None


@pytest.mark.asyncio
async def test_run_once_swallows_a_failing_pass():
    scheduler = ConsolidationScheduler(FakeOrchestrator(raises=True), interval_seconds=60.0)  # type: ignore[arg-type]

    assert await scheduler.run_once() is None
    assert scheduler.passes == 1


def test_start_is_a_no_op_when_the_interval_disables_it():
    scheduler = ConsolidationScheduler(FakeOrchestrator(), interval_seconds=0.0)  # type: ignore[arg-type]

    assert scheduler.start() is False
    assert scheduler.running is False


@pytest.mark.asyncio
async def test_loop_runs_a_pass_per_interval_then_stops():
    orch = FakeOrchestrator()
    ticks = asyncio.Event()

    async def fake_sleep(_seconds: float) -> None:
        ticks.set()
        await asyncio.sleep(0)

    scheduler = ConsolidationScheduler(
        orch,  # type: ignore[arg-type]
        interval_seconds=1.0,
        sleep=fake_sleep,
    )
    assert scheduler.start() is True
    await ticks.wait()
    await asyncio.sleep(0)
    await scheduler.stop()

    assert scheduler.running is False
    assert orch.calls  # at least one pass ran


@pytest.mark.asyncio
async def test_stop_is_idempotent_and_safe_before_start():
    scheduler = ConsolidationScheduler(FakeOrchestrator(), interval_seconds=1.0)  # type: ignore[arg-type]

    await scheduler.stop()
    await scheduler.stop()


def test_interval_from_env_defaults_reads_and_rejects(monkeypatch):
    monkeypatch.delenv("METAFORGE_CONSOLIDATION_INTERVAL_SECONDS", raising=False)
    assert interval_seconds_from_env() == DEFAULT_INTERVAL_SECONDS

    monkeypatch.setenv("METAFORGE_CONSOLIDATION_INTERVAL_SECONDS", "120")
    assert interval_seconds_from_env() == 120.0

    # 0 is the documented off switch, not a busy loop.
    monkeypatch.setenv("METAFORGE_CONSOLIDATION_INTERVAL_SECONDS", "0")
    assert interval_seconds_from_env() == 0.0

    # A negative value would make asyncio.sleep return instantly, spinning the
    # LLM-backed pass as fast as the event loop allows.
    monkeypatch.setenv("METAFORGE_CONSOLIDATION_INTERVAL_SECONDS", "-30")
    assert interval_seconds_from_env() == 0.0

    monkeypatch.setenv("METAFORGE_CONSOLIDATION_INTERVAL_SECONDS", "not-a-number")
    assert interval_seconds_from_env() == DEFAULT_INTERVAL_SECONDS
