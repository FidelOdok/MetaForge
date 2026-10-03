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
full split): duplicate-commit guards / infeasibility heuristics beyond what
the optimizer itself already returns / cost-budget policy tuning
(FORGE-291); wiring gate evaluators to block the loop on evidence gaps
(FORGE-290); an eval harness (FORGE-292); true multi-objective/Pareto-front
search (FORGE-288's own real scope is single-objective-with-constraints,
matching the ticket's own literal example -- see FORGE-288's PR for the
full split). This module's own "iteration budget" is exactly
``twin_core.prediction.optimizer``'s existing ``max_iterations`` (default
60) -- not a second, separate budget concept.

FORGE-288 (gap G-G2) generalized ``start()``'s own candidate ingestion
beyond ``wall_thickness_mm`` -- it used to read ``c["wall_thickness_mm"]``/
``c["mass_kg"]``/``c["deflection_margin_mm"]``/``c["sf_margin"]`` directly
off each candidate dict, which only ever matched
``make_wall_thickness_optimizer``'s own shape. ``parameter_name``/
``metric``/``candidate_mapper`` make that ingestion generic, defaulting to
the EXACT prior wall-thickness behavior (verified by a regression test
reproducing this session's own live-validated numbers unchanged) -- a
second real optimizer, ``make_tube_height_optimizer`` (sweeps ``height_mm``
with wall thickness fixed, same real hollow-tube physics), proves the
generalization is real, not a rename.

FORGE-289 (gap G-G3): ``start()`` now links the Decision the optimizer
recorded (when it recorded one) to the winning ``DesignLoopIteration`` via
``EdgeType.GENERATED_FROM`` -- so a reader can walk from a converged loop's
winner straight to the Decision it produced, not just infer the connection
from both existing.

FORGE-291 (gap G-G5): a duplicate-commit guard -- calling ``start()`` again
with byte-identical inputs (same ``work_product_id`` + every ``optimize_kwargs``
value except ``record_decision``, which doesn't change the search) now
short-circuits to the PRIOR loop's already-persisted result instead of
re-running the bisection and re-writing a whole new iteration subtree.
Same MET-506 "identical inputs = the same real-world thing" precedent
``decision_recorder.py`` already established, applied here against a
sha256 of the inputs (``loop_inputs_hash``, stamped on iteration 0 of every
run) since a design loop has no single work-product node of its own to
hash content against. Infeasibility detection needed no new mechanism --
``status="infeasible"`` already existed (FORGE-287); this ticket's real
remaining gap was duplicate detection and iteration-budget visibility (see
``api_gateway/twin/optimizer.py`` for the latter). "Cost/iteration budget"
in tokens is aspirational Jira wording that doesn't correspond to anything
real here (this loop makes zero LLM calls -- pure deterministic bisection
math); the honest budget already in play is ``max_iterations``, now
surfaced in the response alongside the already-present ``iteration_count``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import structlog

from observability.tracing import get_tracer
from twin_core.models.design_loop_iteration import DesignLoopIteration
from twin_core.models.enums import EdgeType

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.design_loop")


def _loop_inputs_hash(work_product_id: str, optimize_kwargs: dict[str, Any]) -> str:
    """sha256 of the exact inputs that determine a loop's result --
    ``record_decision`` excluded (it toggles a side effect, not the search
    itself, so two calls differing only in it are still the same loop)."""
    payload = {k: v for k, v in optimize_kwargs.items() if k != "record_decision"}
    payload["work_product_id"] = work_product_id
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _default_wall_thickness_mapper(c: dict[str, Any]) -> tuple[float, dict[str, float]]:
    """The exact mapping ``start()`` always used before FORGE-288 -- kept as
    the default so every existing caller (``make_wall_thickness_optimizer``,
    the MCP tool, the REST route) is unaffected by the generalization."""
    return c["wall_thickness_mm"], {
        "deflection_margin_mm": c["deflection_margin_mm"],
        "sf_margin": c["sf_margin"],
    }


def tube_height_candidate_mapper(c: dict[str, Any]) -> tuple[float, dict[str, float]]:
    """FORGE-288: the mapping for ``make_tube_height_optimizer``'s own
    candidate shape (``height_mm`` instead of ``wall_thickness_mm``) --
    pass as ``candidate_mapper`` alongside ``parameter_name="height_mm"``."""
    return c["height_mm"], {
        "deflection_margin_mm": c["deflection_margin_mm"],
        "sf_margin": c["sf_margin"],
    }


def make_design_loop_starter(
    twin: Any,
    *,
    optimize: Any = None,
    evidence_recorder: Any = None,
    decision_recorder: Any = None,
    parameter_name: str = "wall_thickness_mm",
    metric: str = "mass_kg",
    candidate_mapper: Callable[[dict[str, Any]], tuple[float, dict[str, float]]] | None = None,
) -> Any:
    """Return an async ``start(**optimize_kwargs)`` bound to a twin + an
    optimizer (the wall-thickness one, built fresh from
    ``evidence_recorder``/``decision_recorder``, when ``optimize`` isn't
    supplied directly -- same defaulting a caller would otherwise have to
    duplicate).

    ``parameter_name``/``metric``/``candidate_mapper`` (FORGE-288) let a
    DIFFERENT optimizer (e.g. ``make_tube_height_optimizer``) plug into the
    exact same persistence/SUPERSEDES/CONSTRAINED_BY/approve machinery --
    ``candidate_mapper`` takes one raw candidate dict from ``optimize``'s
    own ``candidates`` list and returns ``(parameter_value,
    constraints_status)``; ``optimize_kwargs`` are forwarded to ``optimize``
    unchanged, so a differently-shaped optimizer (different bounds args,
    a fixed parameter like ``wall_thickness_mm``, ...) just works without
    this function needing to know its exact signature.
    """
    if optimize is None:
        from api_gateway.twin.optimizer import make_wall_thickness_optimizer

        optimize = make_wall_thickness_optimizer(
            twin, evidence_recorder=evidence_recorder, decision_recorder=decision_recorder
        )
    mapper = candidate_mapper or _default_wall_thickness_mapper

    async def start(**optimize_kwargs: Any) -> dict[str, Any]:
        work_product_id: str = optimize_kwargs["work_product_id"]
        project_id: str | None = optimize_kwargs.get("project_id")
        requirement_ids: list[str] | None = optimize_kwargs.get("requirement_ids")
        optimize_kwargs.setdefault("record_decision", True)
        loop_inputs_hash = _loop_inputs_hash(work_product_id, optimize_kwargs)

        with tracer.start_as_current_span("twin.start_design_loop") as span:
            span.set_attribute("design_loop.loop_inputs_hash", loop_inputs_hash)

            # FORGE-291: duplicate-commit guard. A hit means this exact loop
            # already ran -- return its already-persisted result instead of
            # spending a bisection + a whole new iteration subtree on a
            # re-submission.
            prior_iter0 = await twin.find_design_loop_by_inputs_hash(loop_inputs_hash)
            if prior_iter0 is not None:
                prior_iterations = await twin.list_design_loop_iterations(prior_iter0.loop_id)
                prior_winner = next((it for it in prior_iterations if it.is_winner), None)
                if prior_winner is None:
                    prior_status = "infeasible"
                elif prior_winner.iteration_number == 0:
                    prior_status = "already_feasible_at_min"
                else:
                    prior_status = "optimal"
                logger.info(
                    "design_loop_duplicate_detected",
                    loop_id=str(prior_iter0.loop_id),
                    loop_inputs_hash=loop_inputs_hash,
                )
                return {
                    "status": prior_status,
                    "detail": (
                        f"duplicate of an earlier run with identical inputs "
                        f"(loop_id={prior_iter0.loop_id})"
                    ),
                    "winner": prior_winner.model_dump(mode="json") if prior_winner else None,
                    "loop_id": str(prior_iter0.loop_id),
                    "iteration_count": len(prior_iterations),
                    "iteration_ids": [str(it.id) for it in prior_iterations],
                    # Identical inputs by definition (that's what made this
                    # a duplicate hit) -- the current call's own
                    # max_iterations IS the prior run's.
                    "max_iterations": optimize_kwargs.get("max_iterations", 60),
                    "duplicate": True,
                }

            out = await optimize(**optimize_kwargs)
            candidates: list[dict[str, Any]] = out["candidates"]
            winner: dict[str, Any] | None = out.get("winner")
            status: str = out["status"]

            loop_id = uuid4()
            wp_id = UUID(work_product_id)
            pid = UUID(project_id) if project_id else None
            span.set_attribute("design_loop.loop_id", str(loop_id))
            span.set_attribute("design_loop.status", status)
            span.set_attribute("design_loop.parameter_name", parameter_name)

            # Index-based, not value-based: twin_core.prediction.optimizer's
            # own structure guarantees exactly where the winner sits --
            # ``already_feasible_at_min`` -> candidates[0] (the lo bounds
            # check itself), ``optimal`` -> candidates[-1] (``winner =
            # eval_at(hi); candidates.append(winner)`` right before
            # return). Matching by parameter VALUE instead would be wrong:
            # the final ``eval_at(hi)`` can re-evaluate to the exact same
            # value as the last loop-appended candidate, so more than one
            # candidate can legitimately share that float.
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
                parameter_value, constraints_status = mapper(c)
                iteration = DesignLoopIteration(
                    loop_id=loop_id,
                    iteration_number=i,
                    project_id=pid,
                    work_product_id=wp_id,
                    parameter_name=parameter_name,
                    parameter_value=parameter_value,
                    metric=metric,
                    objective_value=c[metric],
                    constraints_status=constraints_status,
                    feasible=c["feasible"],
                    status=iter_status,
                    is_winner=is_winner,
                    # FORGE-291: stamped on every iteration (cheap, and
                    # matches every OTHER field's per-iteration storage
                    # convention) though only iteration 0 is ever queried
                    # against, by find_design_loop_by_inputs_hash.
                    loop_inputs_hash=loop_inputs_hash,
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

            # FORGE-289 (gap G-G3): the optimizer records a Decision for the
            # winning candidate (when one exists) -- link it to the real
            # DesignLoopIteration node that won, not just the implicit
            # "same call" relationship. GENERATED_FROM (previously declared,
            # never used) reads correctly either direction: this Decision
            # was generated from this iteration.
            decision_node_id = out.get("decision_node_id")
            if decision_node_id is not None and winner_index is not None:
                await twin.add_edge(
                    UUID(decision_node_id),
                    iterations[winner_index].id,
                    EdgeType.GENERATED_FROM,
                )

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
                "duplicate": False,
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

    async def approve(
        *, loop_id: str, approved_by: str, audit: dict[str, Any] | None = None
    ) -> dict[str, Any]:
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
                    **(
                        {
                            "approver_verified": bool(audit.get("verified")),
                            "approval_surface": audit.get("surface"),
                            "approval_on_behalf_of": audit.get("on_behalf_of"),
                        }
                        if audit
                        else {}
                    ),
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
