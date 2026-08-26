"""Isaac Sim tool adapter -- MCP server for PhysX physics + RTX rendering (MET-635/636)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import structlog

from observability.tracing import get_tracer
from tool_registry.mcp_server.handlers import ResourceLimits, ToolManifest
from tool_registry.mcp_server.server import McpToolServer
from tool_registry.tools.isaac_sim.config import IsaacSimConfig
from tool_registry.tools.isaac_sim.dispatch import render_scene as dispatch_render_scene
from tool_registry.tools.isaac_sim.dispatch import run_physics as dispatch_run_physics

logger = structlog.get_logger()
tracer = get_tracer("tool_registry.tools.isaac_sim.adapter")

_COMMAND_SCHEMA = {
    "type": "array",
    "items": {"type": "string"},
    "description": "Full container command to run (caller-supplied -- the exact Isaac Sim "
    "headless script invocation syntax is not verified by this adapter, see module docstrings)",
}


class IsaacSimServer(McpToolServer):
    """Isaac Sim tool adapter.

    One container image (nvcr.io/nvidia/isaac-sim) bundles both PhysX and
    RTX -- provides two tools accordingly:
    - isaac_sim.run_physics: dispatch a PhysX physics job
    - isaac_sim.render_scene: dispatch an RTX render job

    Both require ``accept_eula=true`` explicitly (Isaac Sim's container
    requires ACCEPT_EULA=Y; this adapter never accepts a EULA silently),
    and both raise ``RemoteVolumesUnsupportedError`` if a ``usd_path`` is
    given while dispatching to a remote GPU provider (RunPod/Vast.ai) --
    volume mounting only works via local Docker until MET-489 lands.
    """

    def __init__(self, config: IsaacSimConfig | None = None) -> None:
        super().__init__(adapter_id="isaac_sim", version="0.1.0")
        self.config = config or IsaacSimConfig()
        self._register_tools()

    def _register_tools(self) -> None:
        """Register both Isaac Sim tools."""
        self.register_tool(
            manifest=ToolManifest(
                tool_id="isaac_sim.run_physics",
                adapter_id="isaac_sim",
                name="Run Isaac Sim Physics (PhysX)",
                description="Dispatch a PhysX physics job to the Isaac Sim container",
                capability="physics_simulation",
                input_schema={
                    "type": "object",
                    "properties": {
                        "command": _COMMAND_SCHEMA,
                        "usd_path": {"type": "string"},
                        "compute_provider": {"type": "string"},
                        "accept_eula": {"type": "boolean"},
                        "timeout_seconds": {"type": "integer"},
                    },
                    "required": ["command", "accept_eula"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "success": {"type": "boolean"},
                        "exit_code": {"type": "integer"},
                        "stdout": {"type": "string"},
                        "stderr": {"type": "string"},
                        "duration_seconds": {"type": "number"},
                    },
                },
                phase=3,
                resource_limits=ResourceLimits(
                    max_memory_mb=8192, max_cpu_seconds=1800, max_disk_mb=4096
                ),
            ),
            handler=self.run_physics,
        )

        self.register_tool(
            manifest=ToolManifest(
                tool_id="isaac_sim.render_scene",
                adapter_id="isaac_sim",
                name="Render Isaac Sim Scene (RTX)",
                description="Dispatch an RTX render job to the Isaac Sim container",
                capability="rtx_rendering",
                input_schema={
                    "type": "object",
                    "properties": {
                        "command": _COMMAND_SCHEMA,
                        "usd_path": {"type": "string"},
                        "compute_provider": {"type": "string"},
                        "accept_eula": {"type": "boolean"},
                        "timeout_seconds": {"type": "integer"},
                    },
                    "required": ["command", "accept_eula"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "success": {"type": "boolean"},
                        "exit_code": {"type": "integer"},
                        "stdout": {"type": "string"},
                        "stderr": {"type": "string"},
                        "duration_seconds": {"type": "number"},
                    },
                },
                phase=3,
                resource_limits=ResourceLimits(
                    max_memory_mb=8192, max_cpu_seconds=1800, max_disk_mb=4096
                ),
            ),
            handler=self.render_scene,
        )

    async def run_physics(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Dispatch a PhysX physics job."""
        return await self._dispatch("run_physics", dispatch_run_physics, arguments)

    async def render_scene(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Dispatch an RTX render job."""
        return await self._dispatch("render_scene", dispatch_render_scene, arguments)

    async def _dispatch(
        self,
        tool_name: str,
        fn: Callable[..., Awaitable[dict[str, Any]]],
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        command = arguments.get("command")
        if not command:
            raise ValueError("command is required")
        accept_eula = arguments.get("accept_eula")
        if accept_eula is None:
            raise ValueError("accept_eula is required")

        logger.info(f"Dispatching isaac_sim.{tool_name}", command=command)

        with tracer.start_as_current_span(f"isaac_sim.{tool_name}") as span:
            span.set_attribute("isaac_sim.tool", tool_name)
            try:
                return await fn(
                    command=command,
                    usd_path=arguments.get("usd_path"),
                    compute_provider=arguments.get(
                        "compute_provider", self.config.compute_provider
                    ),
                    timeout_seconds=arguments.get("timeout_seconds", self.config.timeout_seconds),
                    accept_eula=accept_eula,
                )
            except Exception as exc:
                span.record_exception(exc)
                raise
