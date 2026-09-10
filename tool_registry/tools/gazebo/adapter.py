"""Gazebo Sim tool adapter -- MCP server for ROS-native physics/dynamics simulation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

from observability.tracing import get_tracer
from tool_registry.mcp_server.handlers import ResourceLimits, ToolManifest
from tool_registry.mcp_server.server import McpToolServer
from tool_registry.tools.gazebo.config import GazeboConfig
from tool_registry.tools.gazebo.result_parser import StatsParseError, extract_results
from tool_registry.tools.gazebo.solver import run_simulation as solver_run_simulation

logger = structlog.get_logger()
tracer = get_tracer("tool_registry.tools.gazebo.adapter")

_VALID_WORLD_SUFFIXES = (".sdf", ".world")


class GazeboServer(McpToolServer):
    """Gazebo Sim tool adapter.

    Provides three tools:
    - gazebo.run_simulation: run a headless physics/dynamics simulation
    - gazebo.validate_world: validate an SDF/world file's basic structure
    - gazebo.extract_results: parse an existing stats JSON file
    """

    def __init__(self, config: GazeboConfig | None = None) -> None:
        super().__init__(adapter_id="gazebo", version="0.1.0")
        self.config = config or GazeboConfig()
        self._register_tools()

    def _register_tools(self) -> None:
        """Register all Gazebo tools."""
        self.register_tool(
            manifest=ToolManifest(
                tool_id="gazebo.run_simulation",
                adapter_id="gazebo",
                name="Run Gazebo Simulation",
                description="Execute a headless physics/dynamics simulation using Gazebo Sim",
                capability="dynamics_simulation",
                input_schema={
                    "type": "object",
                    "properties": {
                        "world_file": {
                            "type": "string",
                            "description": "Path to .sdf/.world file",
                        },
                        "duration_s": {
                            "type": "number",
                            "description": "Simulated duration to run, in seconds",
                        },
                    },
                    "required": ["world_file", "duration_s"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "sim_time_s": {"type": "number"},
                        "wall_time_s": {"type": "number"},
                        "iterations": {"type": "integer"},
                        "results": {"type": "object"},
                    },
                },
                phase=2,
                resource_limits=ResourceLimits(
                    max_memory_mb=2048, max_cpu_seconds=600, max_disk_mb=512
                ),
            ),
            handler=self.run_simulation,
        )

        self.register_tool(
            manifest=ToolManifest(
                tool_id="gazebo.validate_world",
                adapter_id="gazebo",
                name="Validate Gazebo World",
                description="Validate an SDF/world file's basic structure before simulating",
                capability="world_validation",
                input_schema={
                    "type": "object",
                    "properties": {
                        "world_file": {"type": "string"},
                    },
                    "required": ["world_file"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "valid": {"type": "boolean"},
                        "model_count": {"type": "integer"},
                        "issues": {"type": "array"},
                    },
                },
                phase=2,
                resource_limits=ResourceLimits(max_memory_mb=256, max_cpu_seconds=30),
            ),
            handler=self.validate_world,
        )

        self.register_tool(
            manifest=ToolManifest(
                tool_id="gazebo.extract_results",
                adapter_id="gazebo",
                name="Extract Gazebo Results",
                description="Parse an existing Gazebo stats JSON file into structured JSON",
                capability="result_extraction",
                input_schema={
                    "type": "object",
                    "properties": {
                        "stats_path": {
                            "type": "string",
                            "description": "Path to the stats JSON file",
                        },
                    },
                    "required": ["stats_path"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "sim_time_s": {"type": "number"},
                        "real_time_s": {"type": "number"},
                        "iterations": {"type": "integer"},
                        "model_poses": {"type": "object"},
                    },
                },
                phase=2,
                resource_limits=ResourceLimits(max_memory_mb=256, max_cpu_seconds=30),
            ),
            handler=self.handle_extract_results,
        )

    async def run_simulation(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Execute a headless Gazebo Sim run.

        Validates arguments and delegates to _execute_solver().
        """
        world_file = arguments.get("world_file", "")
        duration_s = arguments.get("duration_s")

        if not world_file:
            raise ValueError("world_file is required")
        if duration_s is None:
            raise ValueError("duration_s is required")

        logger.info("Running Gazebo simulation", world_file=world_file, duration_s=duration_s)

        result = await self._execute_solver(world_file, float(duration_s))
        return result

    async def handle_extract_results(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Parse an existing Gazebo stats JSON file."""
        stats_path = arguments.get("stats_path", "")

        if not stats_path:
            raise ValueError("stats_path is required")

        with tracer.start_as_current_span("gazebo.extract_results") as span:
            span.set_attribute("gazebo.stats_path", stats_path)

            logger.info("Extracting results", stats_path=stats_path)

            try:
                return extract_results(stats_path)
            except Exception as exc:
                span.record_exception(exc)
                raise

    async def validate_world(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Validate world file structure without running a full simulation."""
        world_file = arguments.get("world_file", "")

        if not world_file:
            raise ValueError("world_file is required")

        logger.info("Validating world", world_file=world_file)

        result = await self._validate_world_file(world_file)
        return result

    async def _execute_solver(self, world_file: str, duration_s: float) -> dict[str, Any]:
        """Execute the Gazebo solver via subprocess.

        This method is designed to be easily mockable in tests.
        In production, it invokes the gz binary and parses the results.
        """
        with tracer.start_as_current_span("gazebo.execute_solver") as span:
            span.set_attribute("gazebo.world_file", world_file)
            span.set_attribute("gazebo.duration_s", duration_s)

            try:
                solver_result = await solver_run_simulation(
                    world_file=world_file,
                    duration_s=duration_s,
                    timeout=self.config.max_sim_time,
                    gz_binary=self.config.gz_binary,
                    work_dir=self.config.work_dir,
                    headless=self.config.headless,
                )

                parsed: dict[str, Any] = {}
                stats_files = solver_result.get("result_files", [])
                if stats_files:
                    try:
                        parsed = extract_results(stats_files[0])
                    except StatsParseError as exc:
                        logger.warning("Stats file present but unparseable", error=str(exc))

                return {
                    "sim_time_s": solver_result["sim_time_s"],
                    "wall_time_s": solver_result["wall_time_s"],
                    "iterations": solver_result["iterations"],
                    "result_files": solver_result["result_files"],
                    "results": parsed,
                }

            except Exception as exc:
                span.record_exception(exc)
                raise

    async def _validate_world_file(self, world_file: str) -> dict[str, Any]:
        """Validate world file structure by parsing the SDF/world XML.

        This method is designed to be easily mockable in tests.
        """
        world_path = Path(world_file)
        if not world_path.exists():
            raise FileNotFoundError(f"World file not found: {world_file}")

        issues: list[str] = []
        model_count = 0

        if world_path.suffix not in _VALID_WORLD_SUFFIXES:
            issues.append(
                f"Unexpected extension {world_path.suffix!r}, expected one of "
                f"{_VALID_WORLD_SUFFIXES}"
            )

        content = world_path.read_text(encoding="utf-8", errors="replace")
        if "<sdf" not in content:
            issues.append("No <sdf> root element found")
        if "<world" not in content:
            issues.append("No <world> element found")

        model_count = content.count("<model ") + content.count("<model>")
        if model_count == 0:
            issues.append("No <model> elements found in world")

        return {
            "valid": len(issues) == 0,
            "model_count": model_count,
            "issues": issues,
        }
