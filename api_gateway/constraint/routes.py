"""Constraint-synthesis stub for interactive rigid-group manipulation (MET-519).

When a user drags a rigid group in the 3D viewer and clicks *Apply*, the client
posts the group's delta transform here. A full implementation hands this to the
agent to synthesize a parametric constraint and re-solve the assembly; for
Tier 1 this is a deterministic **stub** that turns the delta into a
human-readable suggestion + a typed constraint, and flags clearly-infeasible
deltas as conflicts. The re-solved-GLB stream is a later tier (MET-520/521).
"""

from __future__ import annotations

import time
from typing import Any, Literal
from uuid import uuid4

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
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


def _suggest(group_name: str, dx: float, dy: float, dz: float) -> SynthesizeResponse:
    """Shared suggestion logic — dominant axis → typed constraint (MET-519/521).

    Used by both the REST synthesize endpoint and the streaming recommendation.
    """
    components = {"dx": dx, "dy": dy, "dz": dz}
    axis_field, axis = max(_AXES, key=lambda a: abs(components[a[0]]))
    magnitude = components[axis_field]

    if all(abs(v) < 1e-6 for v in components.values()):
        return SynthesizeResponse(status="noop", suggestion="No change to apply.")

    if any(abs(v) > _FEASIBLE_LIMIT_MM for v in components.values()):
        reason = (
            f"Δ exceeds the feasible envelope (>{_FEASIBLE_LIMIT_MM:.0f}mm on one axis); "
            "the re-solve would likely produce a collision."
        )
        return SynthesizeResponse(
            status="conflict",
            suggestion="Move rejected — constraint conflict.",
            conflict_reason=reason,
        )

    value = round(magnitude, 2)
    sign = "+" if value >= 0 else ""
    parameter = f"{group_name}_position_{axis}"
    suggestion = (
        f"{group_name} moved {sign}{value}mm along {axis.upper()} → suggest {parameter} = {value}mm"
    )
    return SynthesizeResponse(
        status="ok",
        suggestion=suggestion,
        constraint=ConstraintSuggestion(parameter=parameter, value=value),
    )


@router.post("/synthesize", response_model=SynthesizeResponse)
async def synthesize_constraint(body: SynthesizeRequest) -> SynthesizeResponse:
    """Turn a drag delta into a suggested parametric constraint (stub)."""
    with tracer.start_as_current_span("constraint.synthesize") as span:
        span.set_attribute("constraint.group", body.group_name)
        resp = _suggest(body.group_name, body.delta.dx, body.delta.dy, body.delta.dz)
        logger.info("constraint_synthesized", group=body.group_name, status=resp.status)
        return resp


# ---------------------------------------------------------------------------
# Tier-3 live solve streaming (MET-521) — DE-RISKING PROTOTYPE
#
# Proves the 10 Hz drag→solve→stream round-trip and the cascade/violation
# protocol shape with a *stub* solver (no real geometry/kinematics yet). The
# real incremental constraint solver replaces `_stub_solve` later; the wire
# protocol and session lifecycle are what this slice de-risks.
# ---------------------------------------------------------------------------

# Fraction of the drag delta a constraint-linked "follower" group inherits —
# stand-in for a real distance/mate constraint pulling an adjacent part.
_FOLLOWER_FRACTION = 0.5

# Transient, in-memory only (never persisted to Neo4j) — session id → opened-at.
_solve_sessions: dict[str, float] = {}


def _stub_solve(
    drag_group: str, delta: tuple[float, float, float], follower: str | None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Stub incremental solve: dragged group takes the full delta; an optional
    follower cascades at a fixed fraction; flag a clearance violation past the
    feasible limit. Returns (transforms, constraints)."""
    dx, dy, dz = delta
    transforms: list[dict[str, Any]] = [{"group_name": drag_group, "delta": [dx, dy, dz]}]
    if follower:
        f = _FOLLOWER_FRACTION
        transforms.append({"group_name": follower, "delta": [dx * f, dy * f, dz * f]})

    mag = max(abs(dx), abs(dy), abs(dz))
    if mag > _FEASIBLE_LIMIT_MM:
        constraints = [
            {
                "status": "violated",
                "type": "clearance",
                "value": -round(mag - _FEASIBLE_LIMIT_MM, 2),
                "severity": "warning",
            }
        ]
    else:
        constraints = [
            {
                "status": "satisfied",
                "type": "clearance",
                "value": round(_FEASIBLE_LIMIT_MM - mag, 2),
            }
        ]
    return transforms, constraints


@router.websocket("/solve/stream")
async def solve_stream(ws: WebSocket) -> None:
    """Live solve session (MET-521 prototype).

    Client sends an optional init `{group_name, follower}`, then ~10 Hz ticks
    `{delta: [dx, dy, dz]}`. Server streams back a solve result per tick with
    cascading transforms, constraint statuses, a human-readable recommendation,
    and `solve_ms`. Session state is transient (in-memory); closed on disconnect.
    """
    await ws.accept()
    session_id = str(uuid4())
    _solve_sessions[session_id] = time.time()
    drag_group = "group"
    follower: str | None = None
    await ws.send_json({"type": "session", "session_id": session_id})
    logger.info("solve_stream_opened", session_id=session_id)
    try:
        while True:
            msg: dict[str, Any] = await ws.receive_json()
            if msg.get("group_name"):
                drag_group = str(msg["group_name"])
            if "follower" in msg:
                follower = msg["follower"] or None
            raw = msg.get("delta")
            if not raw:
                continue  # handshake / no-op tick
            vals = [float(x) for x in raw] + [0.0, 0.0, 0.0]
            d = (vals[0], vals[1], vals[2])
            t0 = time.perf_counter()
            transforms, constraints = _stub_solve(drag_group, d, follower)
            rec = _suggest(drag_group, *d)
            await ws.send_json(
                {
                    "type": "solve",
                    "session_id": session_id,
                    "timestamp": time.time(),
                    "transforms": transforms,
                    "constraints": constraints,
                    "recommendation": rec.suggestion,
                    "solve_ms": round((time.perf_counter() - t0) * 1000, 3),
                }
            )
    except WebSocketDisconnect:
        pass
    finally:
        _solve_sessions.pop(session_id, None)
        logger.info("solve_stream_closed", session_id=session_id)
