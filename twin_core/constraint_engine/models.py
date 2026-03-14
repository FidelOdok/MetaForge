"""Result models for constraint evaluation."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from twin_core.models.enums import ConstraintSeverity


class ConstraintViolation(BaseModel):
    """A single constraint that failed evaluation."""

    constraint_id: UUID
    constraint_name: str
    severity: ConstraintSeverity
    message: str
    work_product_ids: list[UUID] = Field(default_factory=list)
    expression: str
    evaluated_at: datetime


class ConstraintEvaluationResult(BaseModel):
    """Aggregate result of evaluating one or more constraints."""

    passed: bool  # False if any ERROR-severity constraint fails
    violations: list[ConstraintViolation] = Field(default_factory=list)
    warnings: list[ConstraintViolation] = Field(default_factory=list)
    evaluated_count: int = 0
    skipped_count: int = 0
    duration_ms: float = 0.0
