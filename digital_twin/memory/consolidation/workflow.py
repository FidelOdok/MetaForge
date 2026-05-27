"""Temporal workflow + activity wrapper for the consolidation pipeline.

Production wiring schedules ``ConsolidationWorkflow`` once at gateway
boot; it loops on a fixed interval (default 30 min, per the MET-454
spec) and calls the ``run_consolidation_pass`` activity on each tick.
Temporal handles crash recovery — if the worker dies mid-iteration,
the next worker resumes from the last checkpoint.

The activity body looks up the orchestrator on a worker-bound
``ConsolidationActivities`` instance so the wire layer never has to
serialize the orchestrator graph. Activities take a
``ConsolidationActivityInput`` (a JSON-safe projection of
``ConsolidationRunRequest``) and return a JSON-safe summary of the
report.

Temporal SDK is optional — the decorators degrade to no-ops when it is
not installed so unit tests can exercise the workflow body directly.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

import structlog
from pydantic import BaseModel, Field

from digital_twin.memory.consolidation.modes import (
    ConsolidationMode,
    ConsolidationRunRequest,
)
from digital_twin.memory.consolidation.orchestrator import (
    ConsolidationOrchestrator,
    ConsolidationReport,
)
from digital_twin.memory.consolidation.themes import ConsolidationTheme
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("digital_twin.memory.consolidation.workflow")

# Try to import the Temporal SDK; degrade gracefully when unavailable.
try:
    from temporalio import activity, workflow

    HAS_TEMPORAL = True
except ImportError:  # pragma: no cover — exercised in environments without temporalio
    HAS_TEMPORAL = False

    class _StubModule:
        @staticmethod
        def defn(cls: Any) -> Any:
            return cls

        @staticmethod
        def run(func: Any) -> Any:
            return func

        @staticmethod
        def info() -> Any:
            return None

        @staticmethod
        def sleep(_seconds: float) -> Any:  # pragma: no cover
            return asyncio.sleep(_seconds)

    workflow = _StubModule()  # type: ignore[assignment]
    activity = _StubModule()  # type: ignore[assignment]


DEFAULT_INTERVAL_SECONDS = 30 * 60  # 30 min, per the MET-454 spec
DEFAULT_MAX_ITERATIONS = 0
"""``0`` = loop forever. Tests pin this to a finite number so the
workflow body terminates."""


# ---------------------------------------------------------------------------
# Wire models
# ---------------------------------------------------------------------------


class ConsolidationActivityInput(BaseModel):
    """JSON-safe projection of ``ConsolidationRunRequest`` for the activity."""

    mode: ConsolidationMode = ConsolidationMode.BACKGROUND
    since: datetime | None = None
    until: datetime | None = None
    project_id: UUID | None = None
    theme: ConsolidationTheme | None = None
    min_importance: float | None = None
    fetch_limit: int | None = None

    def to_request(self) -> ConsolidationRunRequest:
        return ConsolidationRunRequest(
            mode=self.mode,
            since=self.since,
            until=self.until,
            project_id=self.project_id,
            theme=self.theme,
            min_importance=self.min_importance,
            fetch_limit=self.fetch_limit,
        )


class ConsolidationActivityOutput(BaseModel):
    """JSON-safe summary the activity returns to the workflow."""

    mode: ConsolidationMode
    fetched_count: int = 0
    group_count: int = 0
    synthesized_count: int = 0
    accepted_count: int = 0
    rejected_count: int = 0
    revalidated_count: int = 0
    newly_failed_count: int = 0

    @classmethod
    def from_report(cls, report: ConsolidationReport) -> ConsolidationActivityOutput:
        return cls(
            mode=report.mode,
            fetched_count=report.fetched_count,
            group_count=report.group_count,
            synthesized_count=report.synthesized_count,
            accepted_count=report.accepted_count,
            rejected_count=report.rejected_count,
            revalidated_count=report.revalidated_count,
            newly_failed_count=report.newly_failed_count,
        )


class ConsolidationWorkflowInput(BaseModel):
    """Input to the Temporal workflow.

    ``max_iterations=0`` runs forever — the production default. Tests
    pin a finite number so the workflow body terminates.
    """

    activity_input: ConsolidationActivityInput = Field(default_factory=ConsolidationActivityInput)
    interval_seconds: int = Field(default=DEFAULT_INTERVAL_SECONDS, ge=1)
    max_iterations: int = Field(default=DEFAULT_MAX_ITERATIONS, ge=0)


class ConsolidationWorkflowOutput(BaseModel):
    """Aggregated summary across every loop iteration."""

    iterations: int
    total_accepted: int
    total_rejected: int
    per_iteration: list[ConsolidationActivityOutput] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Activity wrapper
# ---------------------------------------------------------------------------


@dataclass
class ConsolidationActivities:
    """Worker-bound activity holder.

    The Temporal worker registers ``activities.run_consolidation_pass``
    after constructing this object with a live orchestrator. The
    dataclass is mutable so tests can swap the orchestrator without
    re-creating the worker.
    """

    orchestrator: ConsolidationOrchestrator | None = None
    _call_log: list[ConsolidationActivityOutput] = field(default_factory=list)

    @property
    def call_log(self) -> list[ConsolidationActivityOutput]:
        return list(self._call_log)

    async def run_consolidation_pass(
        self,
        input: ConsolidationActivityInput,
    ) -> ConsolidationActivityOutput:
        if self.orchestrator is None:
            raise RuntimeError(
                "ConsolidationActivities.orchestrator was not bound before activity ran"
            )
        with tracer.start_as_current_span(
            "consolidation.workflow.activity.run_consolidation_pass"
        ) as span:
            span.set_attribute("memory.mode", input.mode.value)
            request = input.to_request()
            report = await self.orchestrator.run_request(request)
            output = ConsolidationActivityOutput.from_report(report)
            self._call_log.append(output)
            return output


# Module-level activity registration. Temporal needs a callable bound to
# ``@activity.defn`` at import time so the worker can find it by name.
# Production worker bootstrap rebinds this to the real orchestrator via
# ``register_consolidation_activities`` below.
_DEFAULT_ACTIVITIES = ConsolidationActivities()


@activity.defn(name="run_consolidation_pass")
async def run_consolidation_pass_activity(
    input: ConsolidationActivityInput,
) -> ConsolidationActivityOutput:
    """Temporal activity entry — delegates to the bound ``ConsolidationActivities``."""
    return await _DEFAULT_ACTIVITIES.run_consolidation_pass(input)


def register_consolidation_activities(
    orchestrator: ConsolidationOrchestrator,
) -> ConsolidationActivities:
    """Bind a live orchestrator into the module-level activity holder.

    Returns the activities object so the worker can also expose
    ``orchestrator`` for direct inspection in unit tests.
    """
    _DEFAULT_ACTIVITIES.orchestrator = orchestrator
    return _DEFAULT_ACTIVITIES


# ---------------------------------------------------------------------------
# Workflow definition
# ---------------------------------------------------------------------------


@workflow.defn(name="ConsolidationWorkflow")
class ConsolidationWorkflow:
    """Schedules consolidation passes at a fixed cadence (default 30 min)."""

    @workflow.run
    async def run(
        self,
        input: ConsolidationWorkflowInput,
    ) -> ConsolidationWorkflowOutput:
        iterations = 0
        total_accepted = 0
        total_rejected = 0
        per_iteration: list[ConsolidationActivityOutput] = []

        while True:
            if input.max_iterations and iterations >= input.max_iterations:
                break

            output = await _execute_pass(input.activity_input)
            per_iteration.append(output)
            total_accepted += output.accepted_count
            total_rejected += output.rejected_count
            iterations += 1

            if input.max_iterations and iterations >= input.max_iterations:
                break

            await _sleep(input.interval_seconds)

        return ConsolidationWorkflowOutput(
            iterations=iterations,
            total_accepted=total_accepted,
            total_rejected=total_rejected,
            per_iteration=per_iteration,
        )


# ---------------------------------------------------------------------------
# Indirection seams (tests inject fakes via monkeypatch)
# ---------------------------------------------------------------------------


def _in_temporal_workflow_loop() -> bool:
    """Return True when ``workflow.info()`` can resolve a live workflow context.

    Temporal raises ``_NotInWorkflowEventLoopError`` from ``workflow.info()``
    when called outside a workflow runtime — including from unit tests
    that drive the workflow body via plain asyncio. Swallowing the
    exception is the only reliable check the SDK exposes.
    """
    if not HAS_TEMPORAL:
        return False
    try:
        workflow.info()
    except Exception:
        return False
    return True


async def _execute_pass(
    activity_input: ConsolidationActivityInput,
) -> ConsolidationActivityOutput:
    """Invoke the activity.

    Under Temporal, ``workflow.execute_activity`` is the right call. In
    unit tests this is monkey-patched to bypass the worker / temporalio
    runtime entirely.
    """
    if _in_temporal_workflow_loop():  # pragma: no cover - prod path
        from datetime import timedelta as _timedelta

        result: ConsolidationActivityOutput = await workflow.execute_activity(
            run_consolidation_pass_activity,
            activity_input,
            start_to_close_timeout=_timedelta(minutes=10),
        )
        return result
    return await _DEFAULT_ACTIVITIES.run_consolidation_pass(activity_input)


async def _sleep(seconds: int) -> None:
    """Workflow-safe sleep — uses ``workflow.sleep`` under Temporal."""
    if _in_temporal_workflow_loop():  # pragma: no cover - prod path
        await workflow.sleep(seconds)
    else:
        await asyncio.sleep(seconds)
