"""Bounded one-variable search, ask and tell (composable flows follow-up).

``optimize.suggest`` never evaluates an objective itself. Engineering
objectives come from analysis -- a thermal run, an FEA, a power budget -- so
the caller evaluates each point with the real tools and hands the results
back; this module decides which point to evaluate next and when to stop.
That keeps it deterministic and stateless (the evaluations are the state),
and it cannot invent a number: every objective value it reports is one the
caller supplied.

Two methods:

* ``golden_section`` -- minimises (or maximises) a unimodal objective over
  ``[lower, upper]`` to within ``tolerance`` of the variable, replaying the
  textbook bracket from the evaluations it is given. The same evaluations
  always give the same next point.
* ``grid`` -- evaluates ``points`` evenly spaced values and reports the best,
  for an objective that may not be unimodal.

A point marked ``feasible: false`` (a check failed there) is never the best;
when every evaluated point is infeasible the status says so rather than
naming one.
"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, Field, model_validator

__all__ = [
    "Evaluation",
    "SearchRequest",
    "SearchResult",
    "Variable",
    "suggest",
]

#: The inverse golden ratio, (sqrt(5) - 1) / 2.
_INV_PHI = (math.sqrt(5.0) - 1.0) / 2.0


class Variable(BaseModel):
    """The one design variable being searched, with where its bounds came from."""

    name: str = Field(min_length=1)
    lower: float
    upper: float
    unit: str = ""
    #: Where the bounds come from (a requirement, a process limit, the person).
    #: Required: a search over bounds nobody can justify is a guess.
    bounds_source: str = Field(min_length=1)

    @model_validator(mode="after")
    def _ordered(self) -> Variable:
        if not (math.isfinite(self.lower) and math.isfinite(self.upper)):
            raise ValueError("bounds must be finite")
        if self.upper <= self.lower:
            raise ValueError("upper must be greater than lower")
        return self


class Evaluation(BaseModel):
    """One point the caller evaluated with a real analysis."""

    x: float
    objective: float
    feasible: bool = True
    #: What produced the objective (a simulation result id, a tool run).
    source: str = ""


class SearchRequest(BaseModel):
    variable: Variable
    objective_name: str = Field(min_length=1)
    direction: Literal["minimize", "maximize"] = "minimize"
    method: Literal["golden_section", "grid"] = "golden_section"
    #: golden_section: stop when the bracket is narrower than this (variable units).
    tolerance: float | None = Field(default=None, gt=0)
    #: grid: how many evenly spaced points, ends included.
    points: int = Field(default=11, ge=2, le=201)
    max_evaluations: int = Field(default=40, ge=2, le=400)
    evaluations: list[Evaluation] = Field(default_factory=list)


class SearchResult(BaseModel):
    status: Literal["needs_evaluation", "converged", "exhausted", "infeasible"]
    next_x: float | None = None
    best_x: float | None = None
    best_objective: float | None = None
    bracket: tuple[float, float] | None = None
    evaluations_used: int = 0
    reason: str = ""


def _key(x: float, span: float) -> float:
    """Round so a point the caller echoes back matches the one suggested."""
    return round(x / span, 9) * span if span else x


def _lookup(evals: dict[float, Evaluation], x: float, span: float) -> Evaluation | None:
    return evals.get(_key(x, span))


def _score(e: Evaluation, maximize: bool) -> float:
    if not e.feasible or not math.isfinite(e.objective):
        return math.inf
    return -e.objective if maximize else e.objective


def _best(evaluations: list[Evaluation], maximize: bool) -> Evaluation | None:
    feasible = [e for e in evaluations if e.feasible and math.isfinite(e.objective)]
    if not feasible:
        return None
    return min(feasible, key=lambda e: (_score(e, maximize), e.x))


def _finish(
    status: str,
    request: SearchRequest,
    *,
    bracket: tuple[float, float] | None = None,
    reason: str = "",
) -> SearchResult:
    maximize = request.direction == "maximize"
    in_bounds = [
        e for e in request.evaluations if request.variable.lower <= e.x <= request.variable.upper
    ]
    best = _best(in_bounds, maximize)
    if best is None and in_bounds:
        return SearchResult(
            status="infeasible",
            evaluations_used=len(in_bounds),
            bracket=bracket,
            reason="every evaluated point is infeasible; widen the bounds or revisit the design",
        )
    return SearchResult(
        status=status,  # type: ignore[arg-type]
        best_x=None if best is None else best.x,
        best_objective=None if best is None else best.objective,
        bracket=bracket,
        evaluations_used=len(in_bounds),
        reason=reason,
    )


def _ask(request: SearchRequest, x: float, bracket: tuple[float, float] | None) -> SearchResult:
    partial = _finish("needs_evaluation", request, bracket=bracket)
    unit = f" {request.variable.unit}" if request.variable.unit else ""
    return partial.model_copy(
        update={
            "status": "needs_evaluation",
            "next_x": x,
            "reason": f"evaluate {request.objective_name} at {request.variable.name} = {x:g}{unit}",
        }
    )


def _golden(request: SearchRequest) -> SearchResult:
    v = request.variable
    span = v.upper - v.lower
    tolerance = request.tolerance or span * 1e-3
    maximize = request.direction == "maximize"
    evals = {_key(e.x, span): e for e in request.evaluations}
    a, b = v.lower, v.upper
    c = b - _INV_PHI * (b - a)
    d = a + _INV_PHI * (b - a)
    used = 0
    while (b - a) > tolerance:
        fc, fd = _lookup(evals, c, span), _lookup(evals, d, span)
        if fc is None:
            return _ask(request, _key(c, span), (a, b))
        if fd is None:
            return _ask(request, _key(d, span), (a, b))
        used = len(evals)
        if used >= request.max_evaluations:
            return _finish(
                "exhausted",
                request,
                bracket=(a, b),
                reason=f"used {request.max_evaluations} evaluations before the bracket "
                f"narrowed to {tolerance:g}",
            )
        if _score(fc, maximize) <= _score(fd, maximize):
            b, d = d, c
            c = b - _INV_PHI * (b - a)
        else:
            a, c = c, d
            d = a + _INV_PHI * (b - a)
    return _finish(
        "converged",
        request,
        bracket=(a, b),
        reason=f"bracket [{a:g}, {b:g}] is narrower than {tolerance:g}",
    )


def _grid(request: SearchRequest) -> SearchResult:
    v = request.variable
    span = v.upper - v.lower
    evals = {_key(e.x, span): e for e in request.evaluations}
    points = [v.lower + span * i / (request.points - 1) for i in range(request.points)]
    for x in points:
        if _lookup(evals, x, span) is None:
            if len(evals) >= request.max_evaluations:
                return _finish(
                    "exhausted",
                    request,
                    reason=f"used {request.max_evaluations} evaluations of a "
                    f"{request.points}-point grid",
                )
            return _ask(request, _key(x, span), None)
    return _finish("converged", request, reason=f"all {request.points} grid points evaluated")


def suggest(request: SearchRequest) -> SearchResult:
    """The next point to evaluate, or the best one when the search is done."""
    if request.method == "grid":
        return _grid(request)
    return _golden(request)
