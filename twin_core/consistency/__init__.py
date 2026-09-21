"""Engineering consistency: runtime invariants, budget allocation, objective
ranking, dependency-directed staleness, and gate evaluation
(FORGE-57/58/59/60/61/62, Phases 4-5 of epic FORGE-35).
"""

from twin_core.consistency.budgets import BudgetEngine
from twin_core.consistency.gates import (
    GateCheck,
    GateCheckStatus,
    GateEvaluation,
    GateStatus,
    evaluate_g3_feasibility,
    evaluate_g4_architecture,
    evaluate_g5_concept_selection,
    evaluate_g6_design_sketch,
)
from twin_core.consistency.invariants import InvariantEngine
from twin_core.consistency.models import (
    Budget,
    BudgetAllocation,
    BudgetStatus,
    Invariant,
    InvariantComparison,
    InvariantResult,
)
from twin_core.consistency.objectives import (
    Candidate,
    Objective,
    ObjectiveDirection,
    ObjectiveEngine,
    OptimizationResult,
    ScoredCandidate,
    objective_from_entity,
)
from twin_core.consistency.staleness import (
    Dependency,
    StaleMarking,
    StalenessEngine,
    StalenessStatus,
)

__all__ = [
    "Budget",
    "BudgetAllocation",
    "BudgetEngine",
    "BudgetStatus",
    "Candidate",
    "Dependency",
    "GateCheck",
    "GateCheckStatus",
    "GateEvaluation",
    "GateStatus",
    "Invariant",
    "InvariantComparison",
    "InvariantEngine",
    "InvariantResult",
    "Objective",
    "ObjectiveDirection",
    "ObjectiveEngine",
    "OptimizationResult",
    "ScoredCandidate",
    "StaleMarking",
    "StalenessEngine",
    "StalenessStatus",
    "evaluate_g3_feasibility",
    "evaluate_g4_architecture",
    "evaluate_g5_concept_selection",
    "evaluate_g6_design_sketch",
    "objective_from_entity",
]
