"""MCP adapter for wheeled mobile-robot drive sizing and kinematics (FORGE-671).

``robot.size_drive`` and ``robot.diff_drive_kinematics`` are arithmetic over
figures the caller gives, in-process and deterministic like
``power.check_budget``: no container, no credentials, so the adapter
registers on every server unless disabled. Each figure carries its source;
a figure not given is unknown and the verdict is ``not_established``.
"""

from __future__ import annotations

from typing import Any

import structlog

from observability.tracing import get_tracer
from tool_registry.mcp_server.handlers import ResourceLimits, ToolManifest
from tool_registry.mcp_server.server import McpToolServer
from tool_registry.tools.robot.drive import (
    DriveRequest,
    DriveResult,
    KinematicsRequest,
    KinematicsResult,
    diff_drive_kinematics,
    size_drive,
)

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.tools.robot.adapter")

_LIMITS = ResourceLimits(max_memory_mb=128, max_cpu_seconds=5, max_disk_mb=1)


class RobotServer(McpToolServer):
    """Serves ``robot.size_drive`` and ``robot.diff_drive_kinematics``."""

    def __init__(self) -> None:
        super().__init__(adapter_id="robot", version="0.1.0")
        self.register_tool(
            manifest=ToolManifest(
                tool_id="robot.size_drive",
                adapter_id="robot",
                name="Size Mobile Robot Drive",
                description=(
                    "Worst-case drive requirements for a wheeled mobile robot: traction force "
                    "climbing the steepest slope while accelerating, torque and speed per "
                    "driven wheel, and peak power. Give mass, wheel radius, driven wheel "
                    "count, max slope, rolling resistance, max acceleration, top speed and "
                    "drivetrain efficiency, each as {value, source}. With a candidate motor "
                    "(rated torque, no-load speed, gear ratio) and the required torque margin "
                    "from the requirements, it returns the motor's torque and speed margins "
                    "and a pass/fail verdict. A figure you do not give is unknown, never "
                    "assumed: the verdict is then not_established."
                ),
                capability="drive_sizing",
                input_schema=_inline(DriveRequest.model_json_schema()),
                output_schema=DriveResult.model_json_schema(),
                phase=1,
                resource_limits=_LIMITS,
            ),
            handler=self.size_drive,
        )
        self.register_tool(
            manifest=ToolManifest(
                tool_id="robot.diff_drive_kinematics",
                adapter_id="robot",
                name="Differential Drive Kinematics",
                description=(
                    "Top linear speed and top yaw rate of a differential-drive robot from "
                    "wheel radius, track width, usable motor speed and gear ratio (each "
                    "{value, source}), checked against the required speed and yaw rate from "
                    "the requirements. A figure not given is unknown: not_established."
                ),
                capability="mobile_kinematics",
                input_schema=_inline(KinematicsRequest.model_json_schema()),
                output_schema=KinematicsResult.model_json_schema(),
                phase=1,
                resource_limits=_LIMITS,
            ),
            handler=self.diff_drive_kinematics,
        )

    async def size_drive(self, arguments: dict[str, Any]) -> dict[str, Any]:
        with tracer.start_as_current_span("robot.size_drive") as span:
            request = DriveRequest.model_validate(arguments)
            result = size_drive(request)
            span.set_attribute("robot.verdict", result.verdict)
            logger.info(
                "robot_drive_sized",
                driven_wheels=request.driven_wheels,
                verdict=result.verdict,
                unknowns=len(result.unknowns),
            )
            return result.model_dump(mode="json")

    async def diff_drive_kinematics(self, arguments: dict[str, Any]) -> dict[str, Any]:
        with tracer.start_as_current_span("robot.diff_drive_kinematics") as span:
            request = KinematicsRequest.model_validate(arguments)
            result = diff_drive_kinematics(request)
            span.set_attribute("robot.verdict", result.verdict)
            logger.info(
                "robot_kinematics_checked", verdict=result.verdict, unknowns=len(result.unknowns)
            )
            return result.model_dump(mode="json")


def _inline(schema: dict[str, Any]) -> dict[str, Any]:
    """``$defs`` inlined: some MCP clients do not resolve ``$ref``."""
    defs = schema.pop("$defs", {})

    def inline(node: Any) -> Any:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                return inline(defs[ref.split("/")[-1]])
            return {k: inline(v) for k, v in node.items()}
        if isinstance(node, list):
            return [inline(v) for v in node]
        return node

    inlined: dict[str, Any] = inline(schema)
    return inlined
