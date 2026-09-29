"""Closed design loop orchestration (FORGE-287, gap G-G1, target lifecycle
spec's "dual state machine: propose -> constraint engine -> commit/reject
-> next iteration").

Does not reimplement the search: composes the already-shipped, already
live-validated ``make_wall_thickness_optimizer`` (FORGE-320) -- which
already runs a real propose -> evaluate -> revise -> repeat bisection to
convergence or proven infeasibility, with an iteration budget. FORGE-320's
own gap is that the resulting trace lives only inside one opaque Evidence
``result`` blob, with no per-candidate graph entity a caller can list or
approve. This module adds exactly that:

- ``start_design_loop``: runs the optimizer once (unchanged), then persists
  EVERY candidate it evaluated as a real ``DesignLoopIteration`` node
  (``twin_core.models.design_loop_iteration``), linked in sequence via
  ``EdgeType.SUPERSEDES`` and to the requirement(s) it was evaluated
  against via ``EdgeType.CONSTRAINED_BY``. The winning candidate (if any)
  is marked ``is_winner``/``status="converged"``; the last candidate of an
  infeasible search is marked ``status="infeasible"``.
- ``get_design_loop``: the full iteration timeline for one loop run, for
  the dashboard's "iteration timeline" requirement.
- ``approve_design_loop``: records a human's approval of the winning
  candidate -- the "with a human approving at gates" half of this ticket's
  own yardstick line. Mirrors ``twin.record_decision``'s own honesty
  precedent: approving a loop that never converged (no winner) is a real
  ValueError, not a silently-accepted no-op.

Deliberately out of scope (see this ticket's own PR description for the
full split): generalising the search beyond a single scalar parameter
(FORGE-288); duplicate-commit guards / infeasibility heuristics beyond what
the optimizer itself already returns / cost-budget policy tuning
(FORGE-291); wiring gate evaluators to block the loop on evidence gaps
(FORGE-290); an eval harness (FORGE-292). This module's own "iteration
budget" is exactly ``twin_core.prediction.optimizer``'s existing
``max_iterations`` (default 60) -- not a second, separate budget concept.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import structlog

from observability.tracing import get_tracer
from twin_core.models.design_loop_iteration import DesignLoopIteration
from twin_core.models.enums import EdgeType

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.design_loop")


def make_design_loop_starter(
    twin: Any, *, optimize: Any = None, evidence_recorder: Any = None, decision_recorder: Any = None
) -> Any:
    """Return an async ``start(...)`` bound to a twin + the wall-thickness
    optimizer (built fresh from ``evidence_recorder``/``decision_recorder``
    when ``optimize`` isn't supplied directly -- same defaulting a caller
    would otherwise have to duplicate)."""
    if optimize is None:
        from api_gateway.twin.optimizer import make_wall_thickness_optimizer

        optimize = make_wall_thickness_optimizer(
            twin, evidence_recorder=evidence_recorder, decision_recorder=decision_recorder
        )

    async def start(
        *,
        work_product_id: str,
        load_n: float,
        deflection_limit_mm: float,
        sf_limit: float = 2.0,
        material: str = "aluminum_6061",
        wall_min_mm: float = 0.5,
        wall_max_mm: float | None = None,
        project_id: str | None = None,
        requirement_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        with tracer.start_as_current_span("twin.start_design_loop") as span:
            out = await optimize(
                work_product_id=work_product_id,
                load_n=load_n,
                deflection_limit_mm=deflection_limit_mm,
                sf_limit=sf_limit,
                material=material,
                wall_min_mm=wall_min_mm,
                wall_max_mm=wall_max_mm,
                project_id=project_id,
                requirement_ids=requirement_ids,
                record_decision=True,
            )
            candidates: list[dict[str, Any]] = out["candidates"]
            winner: dict[str, Any] | None = out.get("winner")
            status: str = out["status"]

            loop_id = uuid4()
            wp_id = UUID(work_product_id)
            pid = UUID(project_id) if project_id else None
            span.set_attribute("design_loop.loop_id", str(loop_id))
            span.set_attribute("design_loop.status", status)

            # Index-based, not value-based: twin_core.prediction.optimizer's
            # own structure guarantees exactly where the winner sits --
            # ``already_feasible_at_min`` -> candidates[0] (the lo bounds
            # check itself), ``optimal`` -> candidates[-1] (``winner =
            # eval_at(hi); candidates.append(winner)`` right before
            # return). Matching by wall_thickness_mm VALUE instead would be
            # wrong: the final ``eval_at(hi)`` can re-evaluate to the exact
            # same value as the last loop-appended candidate, so more than
            # one candidate can legitimately share that float.
            winner_index: int | None = None
            if winner is not None and status == "optimal":
                winner_index = len(candidates) - 1
            elif winner is not None and status == "already_feasible_at_min":
                winner_index = 0

            iterations: list[DesignLoopIteration] = []
            for i, c in enumerate(candidates):
                is_last = i == len(candidates) - 1
                is_winner = i == winner_index
                if status == "infeasible":
                    iter_status = "infeasible" if is_last else "candidate"
                else:
                    iter_status = "converged" if is_winner else "candidate"
                iteration = DesignLoopIteration(
                    loop_id=loop_id,
                    iteration_number=i,
                    project_id=pid,
                    work_product_id=wp_id,
                    parameter_name="wall_thickness_mm",
                    parameter_value=c["wall_thickness_mm"],
                    metric="mass_kg",
                    objective_value=c["mass_kg"],
                    constraints_status={
                        "deflection_margin_mm": c["deflection_margin_mm"],
                        "sf_margin": c["sf_margin"],
                    },
                    feasible=c["feasible"],
                    status=iter_status,
                    is_winner=is_winner,
                )
                created = await twin.create_design_loop_iteration(iteration)
                iterations.append(created)

            # Sequence: each iteration supersedes the one before it -- same
            # edge FORGE-321's revalidation flow already uses for "this is
            # the newer replacement of that".
            for prev, cur in zip(iterations, iterations[1:], strict=False):
                await twin.add_edge(cur.id, prev.id, EdgeType.SUPERSEDES)

            # Link every iteration to the requirement(s) it was evaluated
            # against, so a requirement's own page can show which loop runs
            # touched it.
            if requirement_ids:
                for req_id in requirement_ids:
                    req_uuid = UUID(req_id)
                    for iteration in iterations:
                        await twin.add_edge(iteration.id, req_uuid, EdgeType.CONSTRAINED_BY)

            logger.info(
                "design_loop_started",
                loop_id=str(loop_id),
                work_product_id=work_product_id,
                status=status,
                iteration_count=len(iterations),
            )
            return {
                **out,
                "loop_id": str(loop_id),
                "iteration_count": len(iterations),
                "iteration_ids": [str(it.id) for it in iterations],
            }

    return start


def make_design_loop_reader(twin: Any) -> Any:
    """Return an async ``get(...)`` bound to a twin -- the full iteration
    timeline for one loop run, for the dashboard's "iteration timeline"
    requirement."""

    async def get(*, loop_id: str) -> dict[str, Any]:
        iterations = await twin.list_design_loop_iterations(UUID(loop_id))
        if not iterations:
            raise ValueError(f"twin.get_design_loop: no iterations found for loop {loop_id!r}")
        return {
            "loop_id": loop_id,
            "iterations": [it.model_dump(mode="json") for it in iterations],
        }

    return get


def make_design_loop_approver(twin: Any) -> Any:
    """Return an async ``approve(...)`` bound to a twin -- records a
    human's approval of the winning candidate. The "with a human approving
    at gates" half of this ticket's own yardstick line."""

    async def approve(*, loop_id: str, approved_by: str) -> dict[str, Any]:
        with tracer.start_as_current_span("twin.approve_design_loop") as span:
            span.set_attribute("design_loop.loop_id", loop_id)
            iterations = await twin.list_design_loop_iterations(UUID(loop_id))
            winner = next((it for it in iterations if it.is_winner), None)
            if winner is None:
                raise ValueError(
                    f"twin.approve_design_loop: loop {loop_id!r} has no winning iteration "
                    "to approve -- it either did not converge (infeasible) or does not exist"
                )
            updated = await twin.update_design_loop_iteration(
                winner.id,
                {
                    "approved": True,
                    "approved_by": approved_by,
                    "approved_at": datetime.now(UTC),
                },
            )
            logger.info(
                "design_loop_approved",
                loop_id=loop_id,
                iteration_id=str(winner.id),
                approved_by=approved_by,
            )
            return updated.model_dump(mode="json")

    return approve
