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

from api_gateway.constraint.freecad_client import FreecadMcpClient, default_freecad_client
from api_gateway.constraint.kinematics import Joint, solve_joint
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.constraint.routes")

router = APIRouter(prefix="/v1/constraint", tags=["constraint"])

# Beyond this single-axis displacement (mm) the stub treats the move as
# clearly infeasible (likely a collision) and returns a conflict.
_FEASIBLE_LIMIT_MM = 500.0

# Gateway→freecad-adapter client for parametric binding (MET-531). Module-level
# so tests can patch it (mirrors api_gateway.twin.routes._twin).
_freecad_client: FreecadMcpClient = default_freecad_client


class DeltaTransform(BaseModel):
    """Translation delta of a dragged group, in world units (mm)."""

    dx: float = 0.0
    dy: float = 0.0
    dz: float = 0.0


class SynthesizeRequest(BaseModel):
    group_name: str = Field(min_length=1)
    delta: DeltaTransform
    # MET-531: when the dragged group maps to a live FreeCAD session object,
    # supplying both binds the suggested parameter into the model (parametric
    # Apply) instead of only returning a suggestion string. Omitted → unchanged
    # suggestion-only behaviour (backward compatible).
    session_id: str | None = None
    obj_id: str | None = None
    # Placement component to drive; defaults to the dominant axis's
    # ``Placement.Base.<axis>`` when omitted.
    property_path: str | None = None


class ConstraintSuggestion(BaseModel):
    parameter: str
    value: float
    unit: str = "mm"


class SynthesizeResponse(BaseModel):
    status: Literal["ok", "conflict", "noop"]
    suggestion: str
    constraint: ConstraintSuggestion | None = None
    conflict_reason: str | None = None
    # MET-531: set when the suggestion was bound into a live FreeCAD model.
    bound: bool = False
    expression: str | None = None
    binding_error: str | None = None


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


def _axis_of(parameter: str) -> str:
    """Recover the dominant axis (``x``/``y``/``z``) from a ``*_position_<axis>`` name."""
    return parameter.rsplit("_", 1)[-1]


@router.post("/synthesize", response_model=SynthesizeResponse)
async def synthesize_constraint(body: SynthesizeRequest) -> SynthesizeResponse:
    """Turn a drag delta into a parametric constraint, and — when the dragged
    group maps to a live FreeCAD session object (``session_id`` + ``obj_id``) —
    **bind it into the model** so Apply re-parameterizes and re-solves (MET-531).

    Without a session/object it stays a suggestion-only stub (MET-519). Binding
    is best-effort: an adapter error leaves the suggestion intact with
    ``bound=False`` and a ``binding_error``.
    """
    with tracer.start_as_current_span("constraint.synthesize") as span:
        span.set_attribute("constraint.group", body.group_name)
        resp = _suggest(body.group_name, body.delta.dx, body.delta.dy, body.delta.dz)
        span.set_attribute("constraint.status", resp.status)

        # Parametric Apply: only when we have something to bind to and the
        # suggestion is actionable.
        if resp.status == "ok" and resp.constraint and body.session_id and body.obj_id:
            axis = _axis_of(resp.constraint.parameter)
            property_path = body.property_path or f"Placement.Base.{axis}"
            try:
                binding = await _freecad_client.apply_parametric_binding(
                    session_id=body.session_id,
                    obj_id=body.obj_id,
                    parameter=resp.constraint.parameter,
                    value=resp.constraint.value,
                    property_path=property_path,
                )
                resp.bound = bool(binding.get("bound"))
                resp.expression = binding.get("expression")
                resp.suggestion = f"{resp.suggestion} — bound {property_path} = {resp.expression}"
            except Exception as exc:  # noqa: BLE001 — Apply must never hard-fail
                span.record_exception(exc)
                resp.binding_error = str(exc)
                logger.warning(
                    "constraint_binding_failed",
                    group=body.group_name,
                    obj_id=body.obj_id,
                    error=str(exc),
                )

        logger.info(
            "constraint_synthesized",
            group=body.group_name,
            status=resp.status,
            bound=resp.bound,
        )
        return resp


# ---------------------------------------------------------------------------
# Tier-3 live solve streaming (MET-521 protocol, MET-530 joint kinematics)
#
# The 10 Hz drag→solve→stream round-trip (session lifecycle + wire protocol)
# landed in MET-521 with a fixed-fraction stub. MET-530 swaps the solver:
# when the client supplies assembly `joints`, each tick runs analytic single-DOF
# kinematics (api_gateway/constraint/kinematics.py) — the dragged follower's
# motion is constrained to what its joint permits, and the allowed-DOF hint is
# streamed back for gizmo clamping (Tier-2, MET-520). With no joints it falls
# back to the original passthrough+follower-fraction stub (backward compatible).
# The authoritative FreeCAD full solve + collision check runs on Apply, not per
# tick (the hybrid model) — that's the follow-up slice.
# ---------------------------------------------------------------------------

# Fraction of the drag delta a constraint-linked "follower" group inherits in
# the *no-joints* fallback — stand-in for a real mate pulling an adjacent part.
_FOLLOWER_FRACTION = 0.5

# Transient, in-memory only (never persisted to Neo4j) — session id → opened-at.
_solve_sessions: dict[str, float] = {}


def _clearance_constraint(mag: float) -> dict[str, Any]:
    """Feasibility/clearance status for a net displacement magnitude (mm)."""
    if mag > _FEASIBLE_LIMIT_MM:
        return {
            "status": "violated",
            "type": "clearance",
            "value": -round(mag - _FEASIBLE_LIMIT_MM, 2),
            "severity": "warning",
        }
    return {
        "status": "satisfied",
        "type": "clearance",
        "value": round(_FEASIBLE_LIMIT_MM - mag, 2),
    }


def _stub_solve(
    drag_group: str, delta: tuple[float, float, float], follower: str | None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """No-joints fallback: dragged group takes the full delta; an optional
    follower cascades at a fixed fraction; flag a clearance violation past the
    feasible limit. Returns (transforms, constraints)."""
    dx, dy, dz = delta
    transforms: list[dict[str, Any]] = [{"group_name": drag_group, "delta": [dx, dy, dz]}]
    if follower:
        f = _FOLLOWER_FRACTION
        transforms.append({"group_name": follower, "delta": [dx * f, dy * f, dz * f]})
    mag = max(abs(dx), abs(dy), abs(dz))
    return transforms, [_clearance_constraint(mag)]


def _joint_solve(
    drag_group: str,
    delta: tuple[float, float, float],
    grab_point: tuple[float, float, float] | None,
    joints: list[Joint],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any] | None]:
    """Joint-aware solve (MET-530): constrain the dragged follower to its joint's
    DOF via analytic kinematics, and report the allowed-DOF hint. Falls through to
    a free move (full delta) when the dragged group has no joint.

    Returns (transforms, constraints, dof_hint).
    """
    joint = next((j for j in joints if j.follower == drag_group), None)
    if joint is None:
        # Dragged group isn't a constrained follower → move freely.
        dx, dy, dz = delta
        mag = max(abs(dx), abs(dy), abs(dz))
        return (
            [{"group_name": drag_group, "delta": [dx, dy, dz]}],
            [_clearance_constraint(mag)],
            None,
        )

    sol = solve_joint(joint, delta, grab_point)
    transforms = [sol.transform_dict()]
    # Net realised displacement magnitude (post-constraint) for the clearance check.
    mag = max(abs(c) for c in sol.delta) if any(sol.delta) else 0.0
    constraints = [
        _clearance_constraint(mag),
        {"status": "satisfied", "type": "joint", "joint": joint.name, "dof": joint.type},
    ]
    return transforms, constraints, sol.dof.to_dict()


def _parse_joints(raw: Any) -> list[Joint]:
    """Parse a list of joint dicts from the init message; skip malformed ones."""
    if not isinstance(raw, list):
        return []
    joints: list[Joint] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            joints.append(Joint.from_dict(item))
        except (KeyError, ValueError, TypeError) as exc:
            logger.warning("solve_stream_bad_joint", error=str(exc))
    return joints


@router.websocket("/solve/stream")
async def solve_stream(ws: WebSocket) -> None:
    """Live solve session (MET-521 prototype).

    Client sends an optional init `{group_name, follower, joints: [...]}`, then
    ~10 Hz ticks `{delta: [dx, dy, dz], grab_point: [x, y, z]}`. When `joints` are
    supplied the dragged follower is constrained to its joint's DOF (MET-530) and
    the response carries a `dof` hint; otherwise the original stub cascade runs.
    Server streams a solve result per tick with transforms, constraint statuses,
    an optional `dof` hint, a recommendation, and `solve_ms`. Session state is
    transient (in-memory); closed on disconnect.
    """
    await ws.accept()
    session_id = str(uuid4())
    _solve_sessions[session_id] = time.time()
    drag_group = "group"
    follower: str | None = None
    joints: list[Joint] = []
    await ws.send_json({"type": "session", "session_id": session_id})
    logger.info("solve_stream_opened", session_id=session_id)
    try:
        while True:
            msg: dict[str, Any] = await ws.receive_json()
            if msg.get("group_name"):
                drag_group = str(msg["group_name"])
            if "follower" in msg:
                follower = msg["follower"] or None
            if "joints" in msg:
                joints = _parse_joints(msg["joints"])
            raw = msg.get("delta")
            if not raw:
                continue  # handshake / no-op tick
            vals = [float(x) for x in raw] + [0.0, 0.0, 0.0]
            d = (vals[0], vals[1], vals[2])
            grab_raw = msg.get("grab_point")
            grab_point: tuple[float, float, float] | None = None
            if isinstance(grab_raw, (list, tuple)) and len(grab_raw) >= 3:
                grab_point = (float(grab_raw[0]), float(grab_raw[1]), float(grab_raw[2]))

            t0 = time.perf_counter()
            dof: dict[str, Any] | None = None
            if joints:
                transforms, constraints, dof = _joint_solve(drag_group, d, grab_point, joints)
            else:
                transforms, constraints = _stub_solve(drag_group, d, follower)
            rec = _suggest(drag_group, *d)
            payload: dict[str, Any] = {
                "type": "solve",
                "session_id": session_id,
                "timestamp": time.time(),
                "transforms": transforms,
                "constraints": constraints,
                "recommendation": rec.suggestion,
                "solve_ms": round((time.perf_counter() - t0) * 1000, 3),
            }
            if dof is not None:
                payload["dof"] = dof
            await ws.send_json(payload)
    except WebSocketDisconnect:
        pass
    finally:
        _solve_sessions.pop(session_id, None)
        logger.info("solve_stream_closed", session_id=session_id)
