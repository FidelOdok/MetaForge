"""ObjectiveEngine (FORGE-58, spec sections 19 Objective Engine, 49 Objective
Schema).

"Objectives shall remain distinct from constraints" and the target schema is
``class Objective(EngineeringEntity): metric, direction, target, priority,
weight`` -- ``EngineeringEntityType`` already includes ``"objective"``
(FORGE-44, Phase 1), so an Objective is NOT a new graph node type here
either: it's an existing ``EngineeringEntity`` with ``entity_type=
"objective"`` whose ``metric``/``direction``/``target``/``priority``/
``weight`` live in ``metadata``, same convention Phase 1 already
established. ``objective_from_entity`` reads that shape back out; this
module otherwise works on the plain ``Objective`` value object so its
ranking algorithms don't need graph access at all.

Candidates (the things being ranked -- design option A vs B vs C) are
caller-supplied ``{id, metrics}`` pairs rather than a new graph entity this
module invents: nothing in this codebase yet has a "named design option
with a metrics dict" primitive, and guessing one into existence here would
be exactly the kind of unrequested scope this epic's own sub-tasks have
consistently avoided. A future ticket that adds real design-option
tracking can feed this engine real candidates; it doesn't need to look
different to do that.

"The selected method shall be recorded (which one was used, and why)" --
every ``OptimizationResult`` carries ``method`` and ``rationale``.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from twin_core.models.engineering_entity import EngineeringEntity


class ObjectiveDirection(StrEnum):
    MINIMIZE = "minimize"
    MAXIMIZE = "maximize"
    TARGET = "target"


class Objective(BaseModel):
    id: str
    metric: str
    direction: ObjectiveDirection
    # Required when direction == "target"; ignored otherwise.
    target: float | None = None
    # Lower number = higher priority (rank 0 is considered first in
    # lexicographic ordering). Independent of `weight`.
    priority: int = 0
    # Required by the weighted-score method; unused by pareto/lexicographic.
    weight: float | None = None


class Candidate(BaseModel):
    id: str
    metrics: dict[str, float]


class ScoredCandidate(BaseModel):
    candidate_id: str
    score: float


class OptimizationResult(BaseModel):
    method: Literal["weighted_score", "pareto_frontier", "lexicographic"]
    rationale: str
    ranked: list[ScoredCandidate] = Field(default_factory=list)  # weighted_score, lexicographic
    pareto_optimal: list[str] = Field(default_factory=list)  # pareto_frontier
    excluded: list[str] = Field(default_factory=list)  # infeasible candidates dropped up front


def objective_from_entity(entity: EngineeringEntity) -> Objective:
    """Read an Objective's fields back out of an EngineeringEntity's
    metadata (spec section 49's schema, stored per Phase 1's convention)."""
    if entity.entity_type != "objective":
        raise ValueError(f"entity {entity.id} is not an objective (got {entity.entity_type!r})")
    md = entity.metadata
    if "metric" not in md or "direction" not in md:
        raise ValueError(f"objective entity {entity.id} metadata missing metric/direction")
    return Objective(
        id=str(entity.id),
        metric=str(md["metric"]),
        direction=ObjectiveDirection(md["direction"]),
        target=md.get("target"),
        priority=int(md.get("priority", 0)),
        weight=md.get("weight"),
    )


def _desirability(
    direction: ObjectiveDirection, value: float, lo: float, hi: float, target: float | None
) -> float:
    """Normalize `value` to [0, 1] where 1 is always "best" for this
    objective, regardless of direction -- the shared basis every ranking
    method below builds on."""
    if direction == ObjectiveDirection.TARGET:
        if target is None:
            raise ValueError("direction='target' requires Objective.target to be set")
        span = max(abs(hi - target), abs(lo - target), 1e-9)
        return 1.0 - min(abs(value - target) / span, 1.0)
    if hi == lo:
        return 0.5  # every candidate ties on this metric -- neutral, not a fake winner/loser
    norm = (value - lo) / (hi - lo)
    return norm if direction == ObjectiveDirection.MAXIMIZE else 1.0 - norm


class ObjectiveEngine:
    """Ranks Candidates against a set of Objectives. Pure computation --
    no graph access; see the module docstring for why."""

    def __init__(self, objectives: list[Objective]) -> None:
        if not objectives:
            raise ValueError("ObjectiveEngine requires at least one objective")
        self._objectives = objectives

    def _bounds(self, candidates: list[Candidate]) -> dict[str, tuple[float, float]]:
        bounds: dict[str, tuple[float, float]] = {}
        for obj in self._objectives:
            values = [c.metrics[obj.metric] for c in candidates if obj.metric in c.metrics]
            if not values:
                raise ValueError(f"no candidate has a value for objective metric {obj.metric!r}")
            bounds[obj.metric] = (min(values), max(values))
        return bounds

    def _desirabilities(
        self, candidate: Candidate, bounds: dict[str, tuple[float, float]]
    ) -> dict[str, float]:
        out: dict[str, float] = {}
        for obj in self._objectives:
            if obj.metric not in candidate.metrics:
                raise ValueError(f"candidate {candidate.id!r} has no value for {obj.metric!r}")
            lo, hi = bounds[obj.metric]
            out[obj.metric] = _desirability(
                obj.direction, candidate.metrics[obj.metric], lo, hi, obj.target
            )
        return out

    def _feasible(self, candidates: list[Candidate], exclude_ids: list[str]) -> list[Candidate]:
        excluded = set(exclude_ids)
        feasible = [c for c in candidates if c.id not in excluded]
        if not feasible:
            raise ValueError("every candidate was excluded as infeasible -- nothing to rank")
        return feasible

    def weighted_score(
        self, candidates: list[Candidate], *, exclude_ids: list[str] | None = None
    ) -> OptimizationResult:
        if any(obj.weight is None for obj in self._objectives):
            raise ValueError("weighted_score requires every Objective to have a weight")
        feasible = self._feasible(candidates, exclude_ids or [])
        bounds = self._bounds(feasible)
        scored = []
        for c in feasible:
            desirability = self._desirabilities(c, bounds)
            # Weights were already confirmed non-None above (the explicit
            # loop, rather than sum(generator), sidesteps a mypy inference
            # quirk on this exact shape).
            score = 0.0
            for obj in self._objectives:
                assert obj.weight is not None
                score += obj.weight * desirability[obj.metric]
            scored.append(ScoredCandidate(candidate_id=c.id, score=score))
        scored.sort(key=lambda s: s.score, reverse=True)
        weights = ", ".join(f"{o.metric}={o.weight}" for o in self._objectives)
        return OptimizationResult(
            method="weighted_score",
            rationale=f"weighted sum of normalized per-objective desirability ({weights})",
            ranked=scored,
            excluded=list(exclude_ids or []),
        )

    def pareto_frontier(
        self, candidates: list[Candidate], *, exclude_ids: list[str] | None = None
    ) -> OptimizationResult:
        feasible = self._feasible(candidates, exclude_ids or [])
        bounds = self._bounds(feasible)
        desirabilities = {c.id: self._desirabilities(c, bounds) for c in feasible}

        def dominates(a: str, b: str) -> bool:
            da, db = desirabilities[a], desirabilities[b]
            at_least_as_good = all(da[m] >= db[m] for m in da)
            strictly_better = any(da[m] > db[m] for m in da)
            return at_least_as_good and strictly_better

        optimal = [
            c.id
            for c in feasible
            if not any(dominates(other.id, c.id) for other in feasible if other.id != c.id)
        ]
        return OptimizationResult(
            method="pareto_frontier",
            rationale="candidates not dominated on every objective by any other candidate",
            pareto_optimal=optimal,
            excluded=list(exclude_ids or []),
        )

    def lexicographic(
        self, candidates: list[Candidate], *, exclude_ids: list[str] | None = None
    ) -> OptimizationResult:
        feasible = self._feasible(candidates, exclude_ids or [])
        bounds = self._bounds(feasible)
        ordered_objectives = sorted(self._objectives, key=lambda o: o.priority)

        def sort_key(c: Candidate) -> tuple[float, ...]:
            d = self._desirabilities(c, bounds)
            return tuple(-d[obj.metric] for obj in ordered_objectives)  # ascending sort, best first

        ranked_candidates = sorted(feasible, key=sort_key)
        # `score` reports only the highest-priority objective's desirability
        # -- lexicographic order isn't reducible to one meaningful float
        # without losing the tie-break levels, so `ranked`'s LIST ORDER
        # (computed above from the full priority tuple) is the actual
        # result; `score` is a display convenience for the primary criterion.
        ranked = [
            ScoredCandidate(
                candidate_id=c.id,
                score=self._desirabilities(c, bounds)[ordered_objectives[0].metric],
            )
            for c in ranked_candidates
        ]
        priority_order = ", ".join(f"{o.metric}(priority={o.priority})" for o in ordered_objectives)
        return OptimizationResult(
            method="lexicographic",
            rationale=f"ranked strictly by objective priority order: {priority_order}",
            ranked=ranked,
            excluded=list(exclude_ids or []),
        )
