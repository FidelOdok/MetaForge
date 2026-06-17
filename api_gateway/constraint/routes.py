"""Constraint-synthesis stub for interactive rigid-group manipulation (MET-519).

When a user drags a rigid group in the 3D viewer and clicks *Apply*, the client
posts the group's delta transform here. A full implementation hands this to the
agent to synthesize a parametric constraint and re-solve the assembly; for
Tier 1 this is a deterministic **stub** that turns the delta into a
human-readable suggestion + a typed constraint, and flags clearly-infeasible
deltas as conflicts. The re-solved-GLB stream is a later tier (MET-520/521).
"""

from __future__ import annotations

from typing import Literal

import structlog
from fastapi import APIRouter
from pydantic import BaseModel, Field

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.constraint.routes")

router = APIRouter(prefix="/v1/constraint", tags=["constraint"])

# Beyond this single-axis displacement (mm) the stub treats the move as
# clearly infeasible (likely a collision) and returns a conflict.
_FEASIBLE_LIMIT_MM = 500.0


class DeltaTransform(BaseModel):
    """Translation delta of a dragged group, in world units (mm)."""

    dx: float = 0.0
    dy: float = 0.0
    dz: float = 0.0


class SynthesizeRequest(BaseModel):
    group_name: str = Field(min_length=1)
    delta: DeltaTransform


class ConstraintSuggestion(BaseModel):
    parameter: str
    value: float
    unit: str = "mm"


class SynthesizeResponse(BaseModel):
    status: Literal["ok", "conflict", "noop"]
    suggestion: str
    constraint: ConstraintSuggestion | None = None
    conflict_reason: str | None = None


_AXES: tuple[tuple[str, str], ...] = (("dx", "x"), ("dy", "y"), ("dz", "z"))


@router.post("/synthesize", response_model=SynthesizeResponse)
async def synthesize_constraint(body: SynthesizeRequest) -> SynthesizeResponse:
    """Turn a drag delta into a suggested parametric constraint (stub)."""
    with tracer.start_as_current_span("constraint.synthesize") as span:
        span.set_attribute("constraint.group", body.group_name)
        d = body.delta
        components = {"dx": d.dx, "dy": d.dy, "dz": d.dz}

        # Dominant axis by absolute displacement.
        axis_field, axis = max(_AXES, key=lambda a: abs(components[a[0]]))
        magnitude = components[axis_field]

        if all(abs(v) < 1e-6 for v in components.values()):
            return SynthesizeResponse(status="noop", suggestion="No change to apply.")

        if any(abs(v) > _FEASIBLE_LIMIT_MM for v in components.values()):
            reason = (
                f"Δ exceeds the feasible envelope (>{_FEASIBLE_LIMIT_MM:.0f}mm on one axis); "
                "the re-solve would likely produce a collision."
            )
            logger.info("constraint_synthesize_conflict", group=body.group_name, reason=reason)
            return SynthesizeResponse(
                status="conflict",
                suggestion="Move rejected — constraint conflict.",
                conflict_reason=reason,
            )

        value = round(magnitude, 2)
        sign = "+" if value >= 0 else ""
        parameter = f"{body.group_name}_position_{axis}"
        suggestion = (
            f"{body.group_name} moved {sign}{value}mm along {axis.upper()} "
            f"→ suggest {parameter} = {value}mm"
        )
        logger.info(
            "constraint_synthesized", group=body.group_name, parameter=parameter, value=value
        )
        return SynthesizeResponse(
            status="ok",
            suggestion=suggestion,
            constraint=ConstraintSuggestion(parameter=parameter, value=value),
        )
