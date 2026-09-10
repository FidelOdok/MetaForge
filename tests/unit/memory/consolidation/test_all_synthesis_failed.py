"""A pass where every synthesis failed is an outage, not a quiet day (MET-727).

The retired Open Router slugs 404'd on every call, the synthesizer caught it
and returned ``None``, and the orchestrator logged
``consolidation_pass_completed`` with ``synthesized=0`` at info level. That is
indistinguishable from "nothing worth recording today", which is how the whole
tier stayed broken while looking fine -- and why ``memory.list_insights``
stayed empty in every deployment even after MET-567 wired the scheduler.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from digital_twin.memory.consolidation.decay import ConfidenceDecay
from digital_twin.memory.consolidation.fetcher import InMemoryEventFetcher
from digital_twin.memory.consolidation.grouper import EventGrouper
from digital_twin.memory.consolidation.modes import ConsolidationMode, ConsolidationRunRequest
from digital_twin.memory.consolidation.orchestrator import ConsolidationOrchestrator
from digital_twin.memory.consolidation.themes import ConsolidationTheme
from digital_twin.memory.consolidation.validator import InsightValidator
from digital_twin.memory.consolidation.writer import InMemoryInsightStore, SemanticMemoryWriter
from digital_twin.memory.models import ConfidenceTier, ExperienceMemory
from digital_twin.memory.store import InMemoryExperienceStore


class _AlwaysFailsSynthesizer:
    """Stands in for the live behaviour: every LLM call 404s, so every group
    comes back ``None``. The synthesizer swallows the error by design."""

    def __init__(self) -> None:
        self.calls = 0

    async def synthesize(self, group):  # noqa: ANN001, ANN202
        self.calls += 1
        return None


class _FailsOnlyTheFirstGroup:
    """A partial failure. The warning must NOT fire here: some groups
    synthesized, so the tier is working and the pass is legitimately quiet
    about the rest."""

    def __init__(self, insight) -> None:  # noqa: ANN001
        self._insight = insight
        self.calls = 0

    async def synthesize(self, group):  # noqa: ANN001, ANN202
        self.calls += 1
        return None if self.calls == 1 else self._insight


class _RecordingLogger:
    """Records structlog-style calls without touching global configuration.

    Reconfiguring structlog's processor chain only reaches loggers that have
    not yet been bound and cached, which makes such a fixture depend on test
    order -- it passes in isolation and fails once something else has used the
    module's logger first. Swapping the attribute is order-independent.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []

    def _record(self, level: str):  # noqa: ANN202
        def _log(event: str, **kw) -> None:  # noqa: ANN003
            self.calls.append((level, event, kw))

        return _log

    def __getattr__(self, name: str):  # noqa: ANN204
        if name in {"debug", "info", "warning", "error", "critical", "exception"}:
            return self._record(name)
        raise AttributeError(name)


def _events(recorder: _RecordingLogger, name: str) -> list[dict]:
    """The kwargs of every call recording ``name``, whatever the level."""
    return [kw for _lvl, ev, kw in recorder.calls if ev == name]


@pytest.fixture
def captured_logs(monkeypatch):
    """Records what ``orchestrator.py`` logs, as (level, event, kwargs)."""
    from digital_twin.memory.consolidation import orchestrator as orchestrator_module

    recorder = _RecordingLogger()
    monkeypatch.setattr(orchestrator_module, "logger", recorder)
    return recorder


async def _store_with_experiences(
    count: int, task_types: tuple[str, ...] = ("validate_stress",)
) -> InMemoryExperienceStore:
    """Seed a store. ``task_types`` cycles, and classify_theme keys on those
    keywords -- so passing two of them yields two groups, which is what the
    partial-failure case needs to be a partial failure at all."""
    store = InMemoryExperienceStore()
    for i in range(count):
        await store.store(
            ExperienceMemory(
                id=uuid4(),
                run_id="r1",
                step_id=f"s{i}",
                agent_code="mechanical",
                task_type=task_types[i % len(task_types)],
                success=True,
                result_summary=f"revision {i} checked out",
                timestamp=datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC),
                importance=0.9,
                confidence=ConfidenceTier.VERBATIM,
            )
        )
    return store


def _orchestrator(store, synthesizer) -> ConsolidationOrchestrator:  # noqa: ANN001
    insight_store = InMemoryInsightStore()
    return ConsolidationOrchestrator(
        fetcher=InMemoryEventFetcher(store),
        grouper=EventGrouper(),
        synthesizer=synthesizer,  # type: ignore[arg-type]
        validator=InsightValidator(),
        writer=SemanticMemoryWriter(insight_store),
        insight_store=insight_store,
        decay=ConfidenceDecay(),
    )


class TestTotalFailureIsLoud:
    @pytest.mark.asyncio
    async def test_a_pass_where_every_group_failed_warns(self, captured_logs):
        store = await _store_with_experiences(6)
        synthesizer = _AlwaysFailsSynthesizer()
        orchestrator = _orchestrator(store, synthesizer)

        report = await orchestrator.run_request(
            ConsolidationRunRequest(mode=ConsolidationMode.BACKGROUND)
        )

        assert synthesizer.calls > 0, "no groups were formed; test proves nothing"
        assert report.synthesized_count == 0

        warnings = _events(captured_logs, "consolidation_pass_all_synthesis_failed")
        assert warnings, "a total synthesis failure must not read as a clean pass"
        assert warnings[0]["groups"] == report.group_count
        # The hint has to name the actual diagnoses, or it is just noise.
        # All three statuses look identical from here -- a pass that
        # synthesised nothing -- so naming only one sends the reader looking
        # in the wrong place. Found that out live: fixing the retired slugs
        # turned 404 into 402, and the hint still said "model slug".
        hint = warnings[0]["hint"]
        for status in ("404", "402", "401"):
            assert status in hint, f"hint does not mention {status}: {hint}"

    @pytest.mark.asyncio
    async def test_the_completion_log_carries_the_failure_count(self, captured_logs):
        store = await _store_with_experiences(6)
        orchestrator = _orchestrator(store, _AlwaysFailsSynthesizer())

        report = await orchestrator.run_request(
            ConsolidationRunRequest(mode=ConsolidationMode.BACKGROUND)
        )

        completed = _events(captured_logs, "consolidation_pass_completed")
        assert completed
        assert completed[0]["synthesis_failures"] == report.group_count

    @pytest.mark.asyncio
    async def test_a_pass_with_no_events_does_not_warn(self, captured_logs):
        """Genuinely nothing to do is not an outage. Warning here would train
        everyone to ignore the warning, which is the bug this replaces."""
        orchestrator = _orchestrator(InMemoryExperienceStore(), _AlwaysFailsSynthesizer())

        await orchestrator.run_request(ConsolidationRunRequest(mode=ConsolidationMode.BACKGROUND))

        assert not _events(captured_logs, "consolidation_pass_all_synthesis_failed")

    @pytest.mark.asyncio
    async def test_a_partial_failure_does_not_warn(self, captured_logs):
        """The threshold is *every* group, not *any* group. Warning on a
        partial failure would make the signal routine, which is precisely how
        the previous info-level log became invisible."""
        from digital_twin.memory.consolidation.insight import Insight, InsightKind

        # Two themes -> two groups. "stress"/"fea" classify as mechanical
        # validation, "drc" as circuit design rule.
        store = await _store_with_experiences(12, ("validate_stress", "run_drc"))
        insight = Insight(
            theme=ConsolidationTheme.MECHANICAL_VALIDATION,
            kind=InsightKind.OBSERVATION,
            narrative="FEA on thin brackets passes when the fillet radius exceeds 2mm",
            confidence=0.9,
            supporting_experience_ids=[uuid4() for _ in range(6)],
        )
        synthesizer = _FailsOnlyTheFirstGroup(insight)
        orchestrator = _orchestrator(store, synthesizer)

        report = await orchestrator.run_request(
            ConsolidationRunRequest(mode=ConsolidationMode.BACKGROUND)
        )

        assert report.group_count >= 2, (
            f"needs >=2 groups for this to be a PARTIAL failure, got "
            f"{report.group_count} -- otherwise the test passes vacuously"
        )
        assert report.synthesized_count > 0, "at least one group must have succeeded"

        assert not _events(captured_logs, "consolidation_pass_all_synthesis_failed")
