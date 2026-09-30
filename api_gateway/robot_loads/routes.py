"""Gateway route for the "Use as load case" button (FORGE-283).

The dashboard's robot viewer already resolves a posed serial chain's world
transforms and per-link mass/CoM client-side (urdf-loader's parsed
`URDFRobot`, after `robot.setJointValue`/`updateMatrixWorld` — see
`dashboard/src/lib/robot-statics.ts`), so this route does no URDF parsing or
forward kinematics of its own. It only bridges that already-posed payload to
`calculix.compute_joint_loads`, mirroring `api_gateway/cad_export/routes.py`'s
own "no generic MCP tool runner" scoping: this is one specific capability's
route, not a general passthrough.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, HTTPException

from api_gateway.robot_loads.schemas import JointLoadRequest, JointLoadResponse, JointLoadResult
from observability.tracing import get_tracer
from skill_registry.mcp_bridge import McpBridge, McpToolError

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.robot_loads")

router = APIRouter(prefix="/v1/robot", tags=["robot-loads"])


def _unwrap(envelope: Any, tool_id: str) -> dict[str, Any]:
    """Same envelope-unwrapping convention as cad_export.routes._unwrap."""
    if not isinstance(envelope, dict):
        return {}
    if envelope.get("status") == "error":
        err = envelope.get("error") or envelope
        raise HTTPException(status_code=502, detail=f"{tool_id} failed: {err}")
    data = envelope.get("data", envelope)
    return data if isinstance(data, dict) else {}


async def _invoke(bridge: McpBridge, tool_id: str, params: dict[str, Any]) -> dict[str, Any]:
    with tracer.start_as_current_span("robot_loads.invoke") as span:
        span.set_attribute("robot_loads.tool_id", tool_id)
        try:
            envelope = await bridge.invoke(tool_id, params)
        except McpToolError as exc:
            logger.warning("robot_loads_tool_failed", tool_id=tool_id, error=exc.details)
            span.record_exception(exc)
            raise HTTPException(status_code=502, detail=f"{tool_id} failed: {exc.details}") from exc
        except Exception as exc:  # noqa: BLE001 — surface a clean 502 with the cause
            logger.warning("robot_loads_tool_failed", tool_id=tool_id, error=str(exc))
            span.record_exception(exc)
            raise HTTPException(status_code=502, detail=f"{tool_id} failed: {exc}") from exc
        data = _unwrap(envelope, tool_id)
        logger.info("robot_loads_tool_succeeded", tool_id=tool_id)
        return data


@router.post("/joint-loads", response_model=JointLoadResponse)
async def compute_joint_loads(body: JointLoadRequest) -> JointLoadResponse:
    from api_gateway.chat.routes import get_mcp_bridge

    bridge = get_mcp_bridge()
    args: dict[str, Any] = {
        "links": [link.model_dump() for link in body.links],
        "joints": [joint.model_dump() for joint in body.joints],
        "payload_mass_kg": body.payload_mass_kg,
    }
    if body.payload_position_world_mm is not None:
        args["payload_position_world_mm"] = list(body.payload_position_world_mm)

    data = await _invoke(bridge, "calculix.compute_joint_loads", args)
    return JointLoadResponse(
        loads=[JointLoadResult(**load) for load in data["loads"]],
        worst_joint=JointLoadResult(**data["worst_joint"]),
    )
