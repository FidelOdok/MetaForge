"""The side-effecting half of a design-flow run (FORGE-401).

Everything the workflow is not allowed to do itself lives here: running an
agent loop, reading the twin, writing to the run store. Activities may be
retried, so each one is written to be safe to run twice.

The dependencies are injected rather than imported, for the same reason the
twin adapter takes recorders: this module sits in ``orchestrator`` and the
things it drives live in the gateway. A worker binds them at start-up.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import structlog
from temporalio import activity

from observability.metrics import collector_for
from orchestrator.design_flow.temporal_flow import GateCheck, PhaseRequest, PhaseResult

logger = structlog.get_logger(__name__)

__all__ = ["DesignFlowActivities", "PhaseRunner"]


#: Runs one phase's agent loop. Returns (summary, artifacts, status).
PhaseRunner = Callable[[PhaseRequest], Awaitable[PhaseResult]]

#: Evaluates a gate's preconditions against the twin.
GateChecker = Callable[[dict[str, Any]], Awaitable[GateCheck]]

#: Publishes "this run is waiting at a gate" wherever a human will see it.
GateAnnouncer = Callable[[str, str, str], Awaitable[None]]

#: ``(run_id, phase_id, project_id, gate_reason) -> notes`` (FORGE-530).
RevisionNotes = Callable[[str, str, str | None, str], Awaitable[list[Any]]]


@dataclass
class DesignFlowActivities:
    """Activity implementations, bound to whatever the process has wired up."""

    phase_runner: PhaseRunner
    gate_checker: GateChecker | None = None
    gate_announcer: GateAnnouncer | None = None
    revision_notes: RevisionNotes | None = None

    #: Seconds between heartbeats while a phase is running. A phase is a long
    #: agent loop; without heartbeats Temporal cannot tell a slow one from a
    #: dead worker, and a crash would hold the activity for its full timeout.
    heartbeat_interval: float = 30.0

    @activity.defn(name="run_phase")
    async def run_phase(self, request: PhaseRequest) -> PhaseResult:
        activity.logger.info("design_flow_phase_start phase=%s", request.phase.id)

        task = asyncio.ensure_future(self.phase_runner(request))
        try:
            while True:
                done, _ = await asyncio.wait({task}, timeout=self.heartbeat_interval)
                if done:
                    break
                # Heartbeat payload doubles as the resume hint on a retry.
                activity.heartbeat(request.phase.id)
        except asyncio.CancelledError:
            # The worker is going away or the activity was cancelled. Stop the
            # agent loop rather than leaving it running against a run nobody
            # is waiting on any more.
            task.cancel()
            raise
        return task.result()

    @activity.defn(name="evaluate_gate")
    async def evaluate_gate(self, payload: dict[str, Any]) -> GateCheck:
        if self.gate_checker is None:
            # No checker wired: say so rather than reporting a clean gate.
            # `checked=False` is what stops the workflow acting on this, and
            # it is the difference between "nothing was wrong" and "nothing
            # was looked at".
            return GateCheck(checked=False, constraints_checked=False, reason="no gate checker")
        return await self.gate_checker(payload)

    @activity.defn(name="announce_gate")
    async def announce_gate(self, payload: dict[str, Any]) -> None:
        metrics = collector_for("metaforge-design-flow-worker")
        if self.gate_announcer is None:
            metrics.record_design_flow_gate_announce("unannounced")
            logger.warning(
                "design_flow_gate_unannounced",
                run_id=payload.get("run_id"),
                gate=payload.get("gate"),
                detail="no announcer wired; nobody will be told this run is waiting",
            )
            return
        try:
            await self.gate_announcer(
                str(payload.get("run_id")),
                str(payload.get("gate")),
                str(payload.get("reason") or ""),
            )
        except Exception as exc:  # noqa: BLE001 - announcing must never block or fail the gate
            # The gate itself is already open and durable in the workflow, so
            # a failed announcement is an alarm, not a reason to fail the run.
            metrics.record_design_flow_gate_announce("failed")
            logger.error(
                "design_flow_gate_announce_failed",
                run_id=payload.get("run_id"),
                gate=payload.get("gate"),
                error=str(exc),
            )
            return
        metrics.record_design_flow_gate_announce("announced")

    @activity.defn(name="collect_revision_notes")
    async def collect_revision_notes(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        """The drafts a gate just turned down, as plain notes for the workflow (FORGE-530).

        The twin read happens here so the workflow stays deterministic; it gets
        back :func:`~orchestrator.design_flow.rework_context.notes_to_dicts`
        data. Never fails: a retry brief without notes is still a retry brief.
        """
        from orchestrator.design_flow.rework_context import notes_to_dicts

        if self.revision_notes is None:
            return []
        try:
            notes = await self.revision_notes(
                str(payload.get("run_id") or ""),
                str(payload.get("phase_id") or ""),
                payload.get("project_id") or None,
                str(payload.get("reason") or ""),
            )
        except Exception as exc:  # noqa: BLE001 - see docstring
            logger.warning(
                "design_flow_revision_notes_failed",
                run_id=payload.get("run_id"),
                phase=payload.get("phase_id"),
                error=str(exc),
            )
            return []
        return notes_to_dicts(notes)

    def all(self) -> list[Any]:
        """The activity callables to register with a worker."""
        return [
            self.run_phase,
            self.evaluate_gate,
            self.announce_gate,
            self.collect_revision_notes,
        ]
