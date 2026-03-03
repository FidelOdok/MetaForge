"""CalculiX FEA tool adapter -- MCP server for finite element analysis."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

from tool_registry.mcp_server.handlers import ResourceLimits, ToolManifest
from tool_registry.mcp_server.server import McpToolServer
from tool_registry.tools.calculix.config import CalculixConfig

logger = structlog.get_logger()


class CalculixServer(McpToolServer):
    """CalculiX FEA tool adapter.

    Provides three tools:
    - calculix.run_fea: Static stress FEA analysis
    - calculix.run_thermal: Thermal analysis (steady-state/transient)
    - calculix.validate_mesh: Validate mesh quality
    """

    def __init__(self, config: CalculixConfig | None = None) -> None:
        super().__init__(adapter_id="calculix", version="0.1.0")
        self.config = config or CalculixConfig()
        self._register_tools()

    def _register_tools(self) -> None:
        """Register all CalculiX tools."""
        self.register_tool(
            manifest=ToolManifest(
                tool_id="calculix.run_fea",
                adapter_id="calculix",
                name="Run FEA Analysis",
                description="Execute finite element stress analysis using CalculiX solver",
                capability="stress_analysis",
                input_schema={
                    "type": "object",
                    "properties": {
                        "mesh_file": {
                            "type": "string",
                            "description": "Path to .inp mesh file",
                        },
                        "load_case": {
                            "type": "string",
                            "description": "Load case identifier",
                        },
                        "analysis_type": {
                            "type": "string",
                            "enum": ["static_stress", "modal"],
                            "description": "Type of analysis",
                        },
                    },
                    "required": ["mesh_file", "load_case", "analysis_type"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "max_von_mises": {
                            "type": "object",
                            "description": "Max stress by region",
                        },
                        "solver_time": {"type": "number"},
                        "mesh_elements": {"type": "integer"},
                    },
                },
                phase=1,
                resource_limits=ResourceLimits(
                    max_memory_mb=2048, max_cpu_seconds=600, max_disk_mb=512
                ),
            ),
            handler=self.run_fea,
        )

        self.register_tool(
            manifest=ToolManifest(
                tool_id="calculix.run_thermal",
                adapter_id="calculix",
                name="Run Thermal Analysis",
                description="Execute thermal analysis using CalculiX solver",
                capability="thermal_analysis",
                input_schema={
                    "type": "object",
                    "properties": {
                        "mesh_file": {"type": "string"},
                        "boundary_conditions": {"type": "object"},
                        "analysis_mode": {
                            "type": "string",
                            "enum": ["steady_state", "transient"],
                        },
                    },
                    "required": ["mesh_file", "boundary_conditions"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "max_temperature": {"type": "number"},
                        "min_temperature": {"type": "number"},
                        "temperature_distribution": {"type": "object"},
                        "solver_time": {"type": "number"},
                    },
                },
                phase=1,
                resource_limits=ResourceLimits(max_memory_mb=2048, max_cpu_seconds=600),
            ),
            handler=self.run_thermal,
        )

        self.register_tool(
            manifest=ToolManifest(
                tool_id="calculix.validate_mesh",
                adapter_id="calculix",
                name="Validate Mesh Quality",
                description="Validate mesh quality metrics (aspect ratio, element types)",
                capability="mesh_validation",
                input_schema={
                    "type": "object",
                    "properties": {
                        "mesh_file": {"type": "string"},
                        "max_aspect_ratio": {"type": "number", "default": 10.0},
                    },
                    "required": ["mesh_file"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "valid": {"type": "boolean"},
                        "element_count": {"type": "integer"},
                        "node_count": {"type": "integer"},
                        "max_aspect_ratio": {"type": "number"},
                        "issues": {"type": "array"},
                    },
                },
                phase=1,
                resource_limits=ResourceLimits(max_memory_mb=512, max_cpu_seconds=60),
            ),
            handler=self.validate_mesh,
        )

    async def run_fea(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Execute CalculiX FEA stress analysis.

        In production, this invokes the ccx binary. For now, it validates
        arguments and delegates to _execute_solver().
        """
        mesh_file = arguments.get("mesh_file", "")
        load_case = arguments.get("load_case", "")
        analysis_type = arguments.get("analysis_type", "static_stress")

        if not mesh_file:
            raise ValueError("mesh_file is required")
        if not load_case:
            raise ValueError("load_case is required")
        if analysis_type not in ("static_stress", "modal"):
            raise ValueError(f"Unsupported analysis type: {analysis_type}")

        logger.info(
            "Running FEA analysis",
            mesh_file=mesh_file,
            load_case=load_case,
            analysis_type=analysis_type,
        )

        result = await self._execute_solver(mesh_file, analysis_type)
        return result

    async def run_thermal(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Execute CalculiX thermal analysis."""
        mesh_file = arguments.get("mesh_file", "")
        boundary_conditions = arguments.get("boundary_conditions", {})
        analysis_mode = arguments.get("analysis_mode", "steady_state")

        if not mesh_file:
            raise ValueError("mesh_file is required")
        if not boundary_conditions:
            raise ValueError("boundary_conditions is required")

        logger.info("Running thermal analysis", mesh_file=mesh_file, mode=analysis_mode)

        result = await self._execute_thermal_solver(
            mesh_file, boundary_conditions, analysis_mode
        )
        return result

    async def validate_mesh(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Validate mesh quality without running a full solve."""
        mesh_file = arguments.get("mesh_file", "")
        max_aspect_ratio = arguments.get("max_aspect_ratio", 10.0)

        if not mesh_file:
            raise ValueError("mesh_file is required")

        logger.info("Validating mesh", mesh_file=mesh_file)

        result = await self._validate_mesh_file(mesh_file, max_aspect_ratio)
        return result

    async def _execute_solver(
        self, mesh_file: str, analysis_type: str
    ) -> dict[str, Any]:
        """Execute CalculiX solver. In production, runs the ccx binary.

        This method is designed to be easily mockable in tests.
        The actual implementation would:
        1. Prepare the .inp file with load case
        2. Run: ccx -i <job_name>
        3. Parse the .frd output file
        4. Return structured results
        """
        _job_name = Path(mesh_file).stem

        # This would be the actual subprocess call in production:
        # result = await asyncio.create_subprocess_exec(
        #     self.config.ccx_binary, "-i", job_name,
        #     cwd=work_dir,
        #     stdout=asyncio.subprocess.PIPE,
        #     stderr=asyncio.subprocess.PIPE,
        # )

        raise NotImplementedError(
            "CalculiX solver execution requires the ccx binary. "
            "Use mock_solver() in tests or install CalculiX for production use."
        )

    async def _execute_thermal_solver(
        self,
        mesh_file: str,
        boundary_conditions: dict[str, Any],
        analysis_mode: str,
    ) -> dict[str, Any]:
        """Execute CalculiX thermal solver. See _execute_solver for notes."""
        raise NotImplementedError(
            "CalculiX thermal solver requires the ccx binary. "
            "Use mock_solver() in tests or install CalculiX for production use."
        )

    async def _validate_mesh_file(
        self, mesh_file: str, max_aspect_ratio: float
    ) -> dict[str, Any]:
        """Validate mesh quality by parsing the .inp file.

        In production, this would parse the mesh file and compute quality metrics.
        """
        raise NotImplementedError(
            "Mesh validation requires parsing .inp files. "
            "Use mock responses in tests or install CalculiX for production use."
        )
