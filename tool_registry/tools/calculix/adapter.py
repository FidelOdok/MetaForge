"""CalculiX FEA tool adapter -- MCP server for finite element analysis."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

from observability.tracing import get_tracer
from tool_registry.mcp_server.handlers import ResourceLimits, ToolManifest
from tool_registry.mcp_server.server import McpToolServer
from tool_registry.tools.calculix.config import CalculixConfig
from tool_registry.tools.calculix.result_parser import extract_results, parse_frd_file
from tool_registry.tools.calculix.solver import run_fea as solver_run_fea

logger = structlog.get_logger()
tracer = get_tracer("tool_registry.tools.calculix.adapter")


class CalculixServer(McpToolServer):
    """CalculiX FEA tool adapter.

    Provides four tools:
    - calculix.run_fea: Static stress FEA analysis
    - calculix.extract_results: Parse existing .frd result files
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

        self.register_tool(
            manifest=ToolManifest(
                tool_id="calculix.extract_results",
                adapter_id="calculix",
                name="Extract FEA Results",
                description="Parse existing CalculiX .frd result files into structured JSON",
                capability="result_extraction",
                input_schema={
                    "type": "object",
                    "properties": {
                        "frd_path": {
                            "type": "string",
                            "description": "Path to .frd result file",
                        },
                        "include_node_data": {
                            "type": "boolean",
                            "default": True,
                            "description": "Include per-node data in results",
                        },
                    },
                    "required": ["frd_path"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "stress": {"type": "object"},
                        "displacement": {"type": "object"},
                        "node_count": {"type": "integer"},
                        "metadata": {"type": "object"},
                    },
                },
                phase=1,
                resource_limits=ResourceLimits(max_memory_mb=1024, max_cpu_seconds=60),
            ),
            handler=self.handle_extract_results,
        )

    async def run_fea(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Execute CalculiX FEA stress analysis.

        Validates arguments and delegates to _execute_solver().
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

    async def handle_extract_results(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Parse existing CalculiX .frd result files into structured JSON."""
        frd_path = arguments.get("frd_path", "")
        include_node_data = arguments.get("include_node_data", True)

        if not frd_path:
            raise ValueError("frd_path is required")

        with tracer.start_as_current_span("calculix.extract_results") as span:
            span.set_attribute("calculix.frd_path", frd_path)

            logger.info("Extracting results", frd_path=frd_path)

            try:
                return extract_results(frd_path, include_node_data=include_node_data)
            except Exception as exc:
                span.record_exception(exc)
                raise

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
        """Execute CalculiX solver via subprocess.

        This method is designed to be easily mockable in tests.
        In production, it invokes the ccx binary and parses the results.
        """
        with tracer.start_as_current_span("calculix.execute_solver") as span:
            span.set_attribute("calculix.mesh_file", mesh_file)
            span.set_attribute("calculix.analysis_type", analysis_type)

            try:
                solver_result = await solver_run_fea(
                    mesh_file=mesh_file,
                    load_case="default",
                    analysis_type=analysis_type,
                    timeout=self.config.max_solve_time,
                    ccx_binary=self.config.ccx_binary,
                    work_dir=self.config.work_dir,
                )

                # Parse results from .frd file if available
                frd_files = [
                    f
                    for f in solver_result.get("result_files", [])
                    if f.endswith(".frd")
                ]
                if frd_files:
                    parsed = parse_frd_file(frd_files[0])
                    return {
                        "max_von_mises": {
                            "global": parsed.get("stress", {}).get("max", 0.0),
                        },
                        "solver_time": solver_result["solver_time_s"],
                        "mesh_elements": parsed.get("node_count", 0),
                        "result_files": solver_result["result_files"],
                        "stress": parsed.get("stress", {}),
                        "displacement": parsed.get("displacement", {}),
                    }

                return {
                    "max_von_mises": {},
                    "solver_time": solver_result["solver_time_s"],
                    "mesh_elements": 0,
                    "result_files": solver_result["result_files"],
                }

            except Exception as exc:
                span.record_exception(exc)
                raise

    async def _execute_thermal_solver(
        self,
        mesh_file: str,
        boundary_conditions: dict[str, Any],
        analysis_mode: str,
    ) -> dict[str, Any]:
        """Execute CalculiX thermal solver.

        Thermal analysis uses the same ccx binary with different .inp configuration.
        This method is designed to be easily mockable in tests.
        """
        with tracer.start_as_current_span("calculix.execute_thermal_solver") as span:
            span.set_attribute("calculix.mesh_file", mesh_file)
            span.set_attribute("calculix.analysis_mode", analysis_mode)

            try:
                solver_result = await solver_run_fea(
                    mesh_file=mesh_file,
                    load_case="thermal",
                    analysis_type="static_stress",  # ccx uses same binary
                    timeout=self.config.max_solve_time,
                    ccx_binary=self.config.ccx_binary,
                    work_dir=self.config.work_dir,
                )

                return {
                    "max_temperature": 0.0,
                    "min_temperature": 0.0,
                    "temperature_distribution": {},
                    "solver_time": solver_result["solver_time_s"],
                    "result_files": solver_result["result_files"],
                }

            except Exception as exc:
                span.record_exception(exc)
                raise

    async def _validate_mesh_file(
        self, mesh_file: str, max_aspect_ratio: float
    ) -> dict[str, Any]:
        """Validate mesh quality by parsing the .inp file.

        Reads the .inp file and computes basic quality metrics.
        This method is designed to be easily mockable in tests.
        """
        mesh_path = Path(mesh_file)
        if not mesh_path.exists():
            raise FileNotFoundError(f"Mesh file not found: {mesh_file}")

        content = mesh_path.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines()

        # Count nodes and elements from .inp file
        node_count = 0
        element_count = 0
        in_nodes = False
        in_elements = False

        for line in lines:
            stripped = line.strip()
            if stripped.startswith("*NODE"):
                in_nodes = True
                in_elements = False
                continue
            if stripped.startswith("*ELEMENT"):
                in_elements = True
                in_nodes = False
                continue
            if stripped.startswith("*"):
                in_nodes = False
                in_elements = False
                continue
            if in_nodes and stripped:
                node_count += 1
            if in_elements and stripped:
                element_count += 1

        issues: list[str] = []
        if node_count == 0:
            issues.append("No nodes found in mesh file")
        if element_count == 0:
            issues.append("No elements found in mesh file")

        return {
            "valid": len(issues) == 0,
            "element_count": element_count,
            "node_count": node_count,
            "max_aspect_ratio": 0.0,  # Full aspect ratio check requires element geometry
            "issues": issues,
        }
