"""Engineering consistency: runtime invariants, budget allocation, objective
ranking, dependency-directed staleness, gate evaluation, requirement
satisfaction claims, and pre-commit impact analysis
(FORGE-57/58/59/60/61/62/63/65/67, Phases 4-7 of epic FORGE-35).
"""

from twin_core.consistency.budgets import BudgetEngine, budget_from_entity
from twin_core.consistency.claims import Claim, ClaimStatus, evaluate_claim
from twin_core.consistency.gates import (
    GateCheck,
    GateCheckStatus,
    GateEvaluation,
    GateStatus,
    evaluate_g3_feasibility,
    evaluate_g4_architecture,
    evaluate_g5_concept_selection,
    evaluate_g6_design_sketch,
    evaluate_g7_verification_readiness,
    evaluate_g8_release,
)
from twin_core.consistency.impact import (
    ImpactConflict,
    ImpactEngine,
    ImpactReport,
    RevalidationStep,
)
from twin_core.consistency.invariants import InvariantEngine, invariant_from_entity
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
    "Claim",
    "ClaimStatus",
    "Dependency",
    "GateCheck",
    "GateCheckStatus",
    "GateEvaluation",
    "GateStatus",
    "ImpactConflict",
    "ImpactEngine",
    "ImpactReport",
    "Invariant",
    "InvariantComparison",
    "InvariantEngine",
    "InvariantResult",
    "Objective",
    "ObjectiveDirection",
    "ObjectiveEngine",
    "OptimizationResult",
    "RevalidationStep",
    "ScoredCandidate",
    "StaleMarking",
    "StalenessEngine",
    "StalenessStatus",
    "budget_from_entity",
    "evaluate_claim",
    "evaluate_g3_feasibility",
    "evaluate_g4_architecture",
    "evaluate_g5_concept_selection",
    "evaluate_g6_design_sketch",
    "evaluate_g7_verification_readiness",
    "evaluate_g8_release",
    "invariant_from_entity",
    "objective_from_entity",
]
