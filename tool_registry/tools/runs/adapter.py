"""Runs MCP adapter — lets the chat agent start gated design flows (MET-587).

Until now a design flow could only be triggered by a human calling
``POST /v1/runs`` (CLI/HTTP); an agent describing a full product in chat
could build twin state but never start the lifecycle. This adapter closes
that seam with two tools built over an injected ``launcher`` (constructed in
the api_gateway layer — same injection pattern as ``decision_recorder`` —
so tool_registry stays free of api_gateway/orchestrator imports).

HITL: launching is not pre-gated because the flow itself pauses at every
phase boundary for human approval — the gates are the approval mechanism.
"""

from __future__ import annotations

from typing import Any

import structlog

from observability.tracing import get_tracer
from tool_registry.mcp_server.handlers import ResourceLimits, ToolManifest
from tool_registry.mcp_server.server import McpToolServer

logger = structlog.get_logger()
tracer = get_tracer("tool_registry.tools.runs.adapter")


class RunsServer(McpToolServer):
    """MCP adapter wrapping the design-flow run launcher."""

    def __init__(self, launcher: Any) -> None:
        super().__init__(adapter_id="run", version="0.1.0")
        self._launcher = launcher
        self._register_tools()

    def _register_tools(self) -> None:
        self.register_tool(
            manifest=ToolManifest(
                tool_id="run.start_design_flow",
                adapter_id="run",
                name="Start Design Flow",
                description=(
                    "Start a gated, multi-phase design lifecycle for a product "
                    "goal (requirements -> architecture -> design -> ... -> "
                    "manufacturing). Use when the user asks to kick off / run / "
                    "start the full design process for something. The run pauses "
                    "at every phase gate for HUMAN approval — starting it queues "
                    "reviewable work, it does not build anything unattended. "
                    "Flows: 'hardware_v1' (full 7-phase hardware/robotics "
                    "lifecycle — the usual choice), 'mech_v1' (mechanical "
                    "vertical: requirements/CAD/FEA), 'design_v1' (thin demo "
                    "vertical). Always pass the project_id when working inside "
                    "a project."
                ),
                capability="run_launch",
                input_schema={
                    "type": "object",
                    "properties": {
                        "goal": {
                            "type": "string",
                            "minLength": 1,
                            "description": "The product/design goal, in full.",
                        },
                        "flow": {
                            "type": "string",
                            "enum": ["hardware_v1", "mech_v1", "design_v1"],
                            "description": "Lifecycle to run (default hardware_v1).",
                        },
                        "project_id": {
                            "type": "string",
                            "description": "Project UUID to scope deliverables to.",
                        },
                        "session_id": {"type": "string", "description": "Originating session."},
                    },
                    "required": ["goal"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "run_id": {"type": "string"},
                        "flow": {"type": "string"},
                        "phases": {"type": "array", "items": {"type": "string"}},
                        "status": {"type": "string"},
                        "note": {"type": "string"},
                    },
                },
                phase=1,
                resource_limits=ResourceLimits(max_memory_mb=256, max_cpu_seconds=15),
            ),
            handler=self.start_design_flow,
        )

        self.register_tool(
            manifest=ToolManifest(
                tool_id="run.get_status",
                adapter_id="run",
                name="Get Run Status",
                description=(
                    "Check a design-flow run's status: current lifecycle state "
                    "(running / awaiting_approval / completed / failed / "
                    "rejected), the gate reason it is paused on, and its result. "
                    "Use after run.start_design_flow to report progress."
                ),
                capability="run_status",
                input_schema={
                    "type": "object",
                    "properties": {
                        "run_id": {"type": "string", "minLength": 1},
                    },
                    "required": ["run_id"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "run_id": {"type": "string"},
                        "status": {"type": "string"},
                        "awaiting_approval_reason": {"type": "string"},
                        "error": {"type": "string"},
                    },
                },
                phase=1,
                resource_limits=ResourceLimits(max_memory_mb=128, max_cpu_seconds=10),
            ),
            handler=self.get_status,
        )

    async def start_design_flow(self, arguments: dict[str, Any]) -> dict[str, Any]:
        goal = arguments.get("goal")
        if not goal or not isinstance(goal, str):
            raise ValueError("run.start_design_flow: 'goal' is required (non-empty string)")
        flow = arguments.get("flow") or "hardware_v1"
        if not isinstance(flow, str):
            raise ValueError("run.start_design_flow: 'flow' must be a string")
        project_id = arguments.get("project_id")
        session_id = arguments.get("session_id")
        with tracer.start_as_current_span("run.start_design_flow") as span:
            span.set_attribute("run.flow", flow)
            result: dict[str, Any] = await self._launcher.start(
                goal=goal,
                flow=flow,
                project_id=project_id if isinstance(project_id, str) else None,
                session_id=session_id if isinstance(session_id, str) else None,
            )
            span.set_attribute("run.id", str(result.get("run_id")))
            return result

    async def get_status(self, arguments: dict[str, Any]) -> dict[str, Any]:
        run_id = arguments.get("run_id")
        if not run_id or not isinstance(run_id, str):
            raise ValueError("run.get_status: 'run_id' is required (non-empty string)")
        with tracer.start_as_current_span("run.get_status") as span:
            span.set_attribute("run.id", run_id)
            out: dict[str, Any] = await self._launcher.status(run_id=run_id)
            return out
