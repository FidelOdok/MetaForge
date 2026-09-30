"""The Temporal worker that actually runs design-flow phases (FORGE-401).

Lives in ``api_gateway`` rather than ``orchestrator`` because of the layering
rule, and the rule is right here: a phase runs the agent brain, which needs
the MCP bridge, the project backend and the twin — all layer-4 things.
``orchestrator`` owns the workflow and the activity *shapes*; this binds real
implementations to them.

Run it with ``python -m api_gateway.runs.flow_worker``.

A note on what a phase activity is. It is one agent loop, minutes to hours,
and it is *not* idempotent in the usual sense: re-running it after a crash
will produce work products again. That is deliberate and it is why the retry
policy is small (3 attempts) rather than generous — the twin tolerates a
duplicate proposal far better than a run tolerates being abandoned halfway,
but neither is free, so retries are bounded and heartbeats are what
distinguish a slow phase from a dead worker.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

from orchestrator.design_flow.temporal_activities import DesignFlowActivities
from orchestrator.design_flow.temporal_flow import GateCheck, PhaseRequest, PhaseResult
from orchestrator.design_flow.worker import build_design_flow_worker

logger = structlog.get_logger(__name__)

__all__ = ["build_activities", "main", "run_worker"]


async def _run_phase(request: PhaseRequest) -> PhaseResult:
    """Drive one phase's agent loop.

    Reuses the same ``HybridBrain`` the in-process executor builds, so the
    two engines run identical phase logic and a difference between them is a
    difference in durability only — not in what the agent does.
    """
    from api_gateway.runs.routes import build_phase_brain
    from orchestrator.design_flow.executor import FlowContext
    from orchestrator.design_flow.spec import Phase

    brain = await build_phase_brain(request.run_id, request.flow_id)
    phase = Phase(
        id=request.phase.id,
        title=request.phase.title,
        objective=request.phase.objective,
        expected_artifacts=tuple(request.phase.expected_artifacts),
        required_deliverables=tuple(request.phase.required_deliverables),
        enforce_deliverables=request.phase.enforce_deliverables,
        disciplines=tuple(request.phase.disciplines),
    )
    ctx = FlowContext(
        goal=request.goal,
        project_id=request.project_id,
        session_id=request.session_id,
    )
    outcome = await brain.run_phase(goal=request.goal, phase=phase, context=ctx)
    return PhaseResult(
        summary=outcome.summary,
        artifacts=list(outcome.artifacts),
        status=outcome.status,
    )


async def _check_gate(payload: dict[str, Any]) -> GateCheck:
    """Evaluate a gate's preconditions against the twin.

    Returns ``checked=False`` when an evaluator is not wired rather than a
    clean result. "Nothing was wrong" and "nothing was looked at" must not
    render the same (FORGE-361).
    """
    from api_gateway.runs.routes import build_gate_checkers

    checkers = await build_gate_checkers()
    if checkers is None:
        return GateCheck(checked=False, constraints_checked=False, reason="no evaluators wired")

    return await checkers.evaluate(payload.get("phase"), payload.get("project_id"))


def build_activities() -> DesignFlowActivities:
    return DesignFlowActivities(phase_runner=_run_phase, gate_checker=_check_gate)


async def run_worker() -> None:
    from api_gateway.runs.engine import temporal_target
    from orchestrator.design_flow.launcher import connect_temporal

    target = temporal_target()
    client = await connect_temporal(target)
    worker = build_design_flow_worker(client, build_activities())
    logger.info("design_flow_worker_starting", target=target)
    await worker.run()


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":  # pragma: no cover
    main()
