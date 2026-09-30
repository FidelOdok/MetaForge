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

from orchestrator.design_flow.temporal_flow import GateCheck, PhaseRequest, PhaseResult

logger = structlog.get_logger(__name__)

__all__ = ["DesignFlowActivities", "PhaseRunner"]


#: Runs one phase's agent loop. Returns (summary, artifacts, status).
PhaseRunner = Callable[[PhaseRequest], Awaitable[PhaseResult]]

#: Evaluates a gate's preconditions against the twin.
GateChecker = Callable[[dict[str, Any]], Awaitable[GateCheck]]

#: Publishes "this run is waiting at a gate" wherever a human will see it.
GateAnnouncer = Callable[[str, str, str], Awaitable[None]]


@dataclass
class DesignFlowActivities:
    """Activity implementations, bound to whatever the process has wired up."""

    phase_runner: PhaseRunner
    gate_checker: GateChecker | None = None
    gate_announcer: GateAnnouncer | None = None

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
        if self.gate_announcer is None:
            logger.warning(
                "design_flow_gate_unannounced",
                run_id=payload.get("run_id"),
                gate=payload.get("gate"),
                detail="no announcer wired; nobody will be told this run is waiting",
            )
            return
        await self.gate_announcer(
            str(payload.get("run_id")),
            str(payload.get("gate")),
            str(payload.get("reason") or ""),
        )

    def all(self) -> list[Any]:
        """The activity callables to register with a worker."""
        return [self.run_phase, self.evaluate_gate, self.announce_gate]
