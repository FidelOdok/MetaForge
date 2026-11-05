"""Input/output schemas for the design_cad_iterative skill."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator


class DesignCadIterativeInput(BaseModel):
    """What the loop refines and how far it may go."""

    project_id: str = Field(..., min_length=1)
    sketch_key: str | None = Field(
        default=None, max_length=80, description="The component's parametric plan (PSK-...)"
    )
    component_id: str | None = Field(
        default=None, max_length=200, description="CMP-... when no sketch_key is given"
    )
    intent: str = Field(default="", max_length=2000, description="What this run should achieve")
    target_dimensions: dict[str, float] | None = Field(
        default=None, description="Overall size the result must have, mm: x, y, z"
    )
    tolerance_mm: float | None = Field(default=None, gt=0, le=10)
    answers: dict[str, float | str] = Field(
        default_factory=dict, description="Answers to an earlier run's questions, by name"
    )
    allow_working_values: bool = Field(
        default=True,
        description="The model may use a labelled working value for an unknown dimension",
    )
    max_iterations: int = Field(default=5, ge=1, le=8)
    max_model_calls: int | None = Field(default=None, ge=1, le=40)
    max_spend_usd: float | None = Field(default=None, gt=0, le=50)
    wait: bool = Field(default=False, description="Wait for the result instead of a job")

    @model_validator(mode="after")
    def _names_a_component(self) -> DesignCadIterativeInput:
        if not (self.sketch_key or self.component_id):
            raise ValueError("name the component's parametric plan (sketch_key) or component_id")
        return self


class DesignCadIterativeOutput(BaseModel):
    """A started job, or (with ``wait``) the finished run."""

    job_id: str | None = None
    status: str = ""
    node_id: str | None = None
    stop_reason: str | None = None
    stop_detail: str | None = None
    iterations: int = 0
    questions: list[dict[str, Any]] = Field(default_factory=list)
    result: dict[str, Any] | None = None
    #: The critic's verdict is a review: a person accepts the proposed version.
    note: str = (
        "Proposed only. A person accepts it (Use this version) or keeps the previous one; "
        "the critic's verdict never validates geometry."
    )
