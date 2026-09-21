"""AgentResult envelope + Unknown model (FORGE-54, spec sections 26.1-26.2,
38 Agent Execution Rules, 61 Unknown Management).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from twin_core.models.patch import Patch


class UnknownSeverity(StrEnum):
    """How much an unresolved question blocks progress (spec section 61)."""

    BLOCKER = "blocker"
    MAJOR = "major"
    MINOR = "minor"


class Unknown(BaseModel):
    """One ranked, unresolved question (spec section 61 Unknown Management).

    ``priority`` implements the spec section 26.2 formula literally:
    ``downstream_impact x uncertainty x cost_of_error x dependency_count``.
    A ``dependency_count`` of 0 legitimately zeroes the priority out -- an
    unknown nothing downstream depends on yet naturally sinks to the bottom
    of the ranking, per the formula as specified, not a bug in this code.
    """

    id: str
    question: str
    severity: UnknownSeverity = UnknownSeverity.MINOR
    affected: list[str] = Field(default_factory=list)
    # spec section 60 Clarification Strategy: "ask only the highest-value
    # unresolved questions needed for the current gate" -- ClarificationAgent
    # uses this to hold non-blocking unknowns back until their gate arrives.
    required_by_gate: str | None = None
    downstream_impact: float = Field(default=0.5, ge=0.0, le=1.0)
    uncertainty: float = Field(default=0.5, ge=0.0, le=1.0)
    cost_of_error: float = Field(default=0.5, ge=0.0, le=1.0)
    dependency_count: int = Field(default=1, ge=0)

    @property
    def priority(self) -> float:
        return (
            self.downstream_impact * self.uncertainty * self.cost_of_error * self.dependency_count
        )


class AgentResult(BaseModel):
    """The response envelope every engineering agent shall produce (spec
    section 38 Agent Execution Rules): never a direct write, always a
    proposed patch plus the reasoning that produced it."""

    conclusions: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    proposed_patch: Patch | None = None
    unresolved: list[Unknown] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
