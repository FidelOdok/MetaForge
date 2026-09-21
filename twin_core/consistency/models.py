"""Invariant/Budget models (FORGE-57, spec sections 17 Runtime Invariants,
18 Constraint Propagation).

Both share the same "metric" computation (see ``metrics.py``): a metric's
current value is the sum of ``metadata[f"{metric}_{unit}"]`` across a
project's WorkProducts -- exactly the convention the pre-existing
constraint_engine's own expressions already use in this codebase (e.g. a
live constraint's real expression: ``all(float(wp.metadata.get('mass_g', 0))
<= 15 for wp in ctx.work_products())``). Nothing new is invented for how a
number gets attached to a WorkProduct; this just gives that existing
convention a name and a reusable aggregate.

An ``Invariant`` is deliberately NOT a new graph node type duplicating
``Constraint`` (which already holds an arbitrary boolean expression this
epic's own gate/transaction machinery evaluates) -- it's a narrower,
numeric-valued companion for the one thing a boolean pass/fail expression
can't give a caller: the actual current number, needed for "predicted =
current + delta" (spec section 17's worked example). ``source_constraint_id``
optionally links an Invariant back to the Constraint node it was "compiled
from", per the spec's own ``source: CON-MASS-001`` field, but nothing
requires one to exist.
"""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field


class InvariantComparison(StrEnum):
    LTE = "<="
    GTE = ">="
    EQ = "=="


class Invariant(BaseModel):
    """A machine-checkable numeric limit (spec section 17)."""

    id: str
    metric: str  # e.g. "mass", "power", "cost"
    unit: str  # e.g. "kg", "w", "gbp" -- metadata key is f"{metric}_{unit}"
    limit: float
    comparison: InvariantComparison = InvariantComparison.LTE
    source_constraint_id: UUID | None = None


class InvariantResult(BaseModel):
    """Outcome of evaluating (or predicting) one Invariant."""

    invariant_id: str
    current: float
    delta: float = 0.0
    predicted: float
    limit: float
    comparison: InvariantComparison
    violated: bool


class BudgetAllocation(BaseModel):
    target: str  # e.g. "frame", "actuators", "battery" -- a free-text label
    amount: float


class Budget(BaseModel):
    """A declared total + its allocation breakdown (spec section 18)."""

    id: str
    project_id: UUID
    metric: str
    unit: str
    system_total: float
    allocations: list[BudgetAllocation] = Field(default_factory=list)


class BudgetStatus(BaseModel):
    """Continuously-calculated budget state (spec section 18: "allocated,
    actual, remaining, margin, violation")."""

    budget_id: str
    system_total: float
    allocated: float  # sum of declared allocations
    actual: float  # sum of real metadata values across the project's WorkProducts
    remaining: float  # system_total - allocated
    margin: float  # system_total - actual
    violation: bool  # actual > system_total
