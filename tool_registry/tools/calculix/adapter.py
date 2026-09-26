"""CalculiX FEA tool adapter -- MCP server for finite element analysis."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

from observability.tracing import get_tracer
from tool_registry.mcp_server.handlers import ResourceLimits, ToolManifest
from tool_registry.mcp_server.server import McpToolServer
from tool_registry.tools.cadquery.materials import resolve_elastic_properties
from tool_registry.tools.calculix.config import CalculixConfig
from tool_registry.tools.calculix.deck_builder import build_static_stress_deck
from tool_registry.tools.calculix.inp_mesh import parse_mesh_inp
from tool_registry.tools.calculix.result_parser import extract_results, parse_frd_file
from tool_registry.tools.calculix.solver import SolverError
from tool_registry.tools.calculix.solver import run_fea as solver_run_fea

logger = structlog.get_logger()
tracer = get_tracer("tool_registry.tools.calculix.adapter")

# FORGE-223: this adapter has no Twin access and none of its tools accept a
# work_product_id -- a mesh_file must already be a path on the shared adapter
# workspace, e.g. freecad.generate_mesh's own 'mesh_file' result. A model that
# only has a work_product_id must call twin.stage_work_product_file first and
# pass its returned file_path here instead; without this hint the schema gave
# no clue why a work_product_id argument kept getting rejected as "missing
# required property mesh_file" (re-test 2026-09-25: 11 identical rejected
# retries across two sessions).
_MESH_FILE_DESCRIPTION = (
    "Path to a .inp mesh file already on the shared adapter workspace "
    "(e.g. freecad.generate_mesh's own 'mesh_file' result). Does NOT accept "
    "a work_product_id -- call twin.stage_work_product_file first if you "
    "only have one, and pass its returned file_path here."
)


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
                            "description": _MESH_FILE_DESCRIPTION,
                        },
                        "load_case": {
                            "type": "string",
                            "description": "Load case label (for logging/naming only).",
                        },
                        "analysis_type": {
                            "type": "string",
                            "enum": ["static_stress", "modal"],
                            "description": (
                                "Type of analysis. FORGE-234: material/fixed_node_set/"
                                "load_node_set/load_force_n below build a complete, "
                                "solvable deck for 'static_stress' only -- 'modal' still "
                                "invokes the mesh directly with no deck construction."
                            ),
                        },
                        "material": {
                            "type": "object",
                            "description": (
                                "Required for 'static_stress'. Either {'name': "
                                "<materials.py name, e.g. 'steel'/'aluminum_6061'>} or "
                                "explicit {'youngs_modulus_mpa': ..., 'poissons_ratio': ...}. "
                                "MPa (N/mm^2), NOT Pa -- mesh coordinates are in "
                                "millimeters, and mixing unit systems silently understates "
                                "stiffness by 1e6."
                            ),
                            "properties": {
                                "name": {"type": "string"},
                                "youngs_modulus_mpa": {"type": "number"},
                                "poissons_ratio": {"type": "number"},
                            },
                        },
                        "fixed_node_set": {
                            "type": "string",
                            "description": (
                                "Required for 'static_stress'. Element set name from "
                                "freecad.generate_mesh's own mesh (e.g. 'Surface1', gmsh's "
                                "per-STEP-face group) to fully constrain (all 3 "
                                "translational DOFs) -- use generate_mesh's own 'faces' "
                                "response to identify which named face is which by its "
                                "bounding box."
                            ),
                        },
                        "load_node_set": {
                            "type": "string",
                            "description": (
                                "Required for 'static_stress'. Element set name to apply "
                                "load_force_n to."
                            ),
                        },
                        "load_force_n": {
                            "type": "array",
                            "items": {"type": "number"},
                            "minItems": 3,
                            "maxItems": 3,
                            "description": (
                                "Required for 'static_stress'. [Fx, Fy, Fz] TOTAL force in "
                                "Newtons, distributed evenly across load_node_set's nodes."
                            ),
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
                        "mesh_file": {"type": "string", "description": _MESH_FILE_DESCRIPTION},
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
                        "mesh_file": {"type": "string", "description": _MESH_FILE_DESCRIPTION},
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

        # FORGE-234: 'static_stress' needs a real, structured load case to
        # build a complete deck around -- there is no meaningful default
        # material/boundary-condition/load, so these are all required here
        # (not in the JSON schema's own 'required' list, since they're only
        # required for THIS analysis_type, not 'modal').
        deck_spec: dict[str, Any] | None = None
        if analysis_type == "static_stress":
            material = arguments.get("material")
            fixed_node_set = arguments.get("fixed_node_set")
            load_node_set = arguments.get("load_node_set")
            load_force_n = arguments.get("load_force_n")
            missing = [
                name
                for name, value in (
                    ("material", material),
                    ("fixed_node_set", fixed_node_set),
                    ("load_node_set", load_node_set),
                    ("load_force_n", load_force_n),
                )
                if not value
            ]
            if missing:
                raise ValueError(
                    f"calculix.run_fea: {', '.join(missing)} required for "
                    "analysis_type='static_stress' -- there is no default material or "
                    "boundary condition/load to build a real analysis deck around."
                )
            if not isinstance(material, dict):
                raise ValueError("calculix.run_fea: 'material' must be an object")
            youngs_modulus_mpa, poissons_ratio = resolve_elastic_properties(
                material=material.get("name"),
                youngs_modulus_mpa=material.get("youngs_modulus_mpa"),
                poissons_ratio=material.get("poissons_ratio"),
            )
            if not (isinstance(load_force_n, list) and len(load_force_n) == 3):
                raise ValueError("calculix.run_fea: 'load_force_n' must be [Fx, Fy, Fz]")
            deck_spec = {
                "youngs_modulus_mpa": youngs_modulus_mpa,
                "poissons_ratio": poissons_ratio,
                "fixed_node_set": fixed_node_set,
                "load_node_set": load_node_set,
                "load_force_n": (
                    float(load_force_n[0]),
                    float(load_force_n[1]),
                    float(load_force_n[2]),
                ),
            }

        logger.info(
            "Running FEA analysis",
            mesh_file=mesh_file,
            load_case=load_case,
            analysis_type=analysis_type,
            deck_spec=deck_spec,
        )

        result = await self._execute_solver(mesh_file, analysis_type, deck_spec)
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

        result = await self._execute_thermal_solver(mesh_file, boundary_conditions, analysis_mode)
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
        self, mesh_file: str, analysis_type: str, deck_spec: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Execute CalculiX solver via subprocess.

        This method is designed to be easily mockable in tests.
        In production, it invokes the ccx binary and parses the results.

        FORGE-234: when ``deck_spec`` is given (always true for
        ``analysis_type='static_stress'`` -- see ``run_fea``), ``mesh_file``
        is treated as mesh-only topology (nodes + elements, no analysis
        cards -- exactly what freecad.generate_mesh produces) and a
        complete, solvable deck is built around it first (only the volume
        elements, real material/section/boundary/load/output-request cards
        -- see ``deck_builder.build_static_stress_deck``), written to a new
        ``<stem>_solved.inp`` file, and THAT is what actually gets solved.
        """
        with tracer.start_as_current_span("calculix.execute_solver") as span:
            span.set_attribute("calculix.mesh_file", mesh_file)
            span.set_attribute("calculix.analysis_type", analysis_type)

            try:
                solved_file = mesh_file
                if deck_spec is not None:
                    mesh = parse_mesh_inp(mesh_file)
                    deck_text = build_static_stress_deck(mesh, **deck_spec)
                    solved_path = Path(mesh_file).with_name(f"{Path(mesh_file).stem}_solved.inp")
                    solved_path.write_text(deck_text, encoding="utf-8")
                    solved_file = str(solved_path)
                    span.set_attribute("calculix.solved_file", solved_file)

                solver_result = await solver_run_fea(
                    mesh_file=solved_file,
                    load_case="default",
                    analysis_type=analysis_type,
                    timeout=self.config.max_solve_time,
                    ccx_binary=self.config.ccx_binary,
                    work_dir=self.config.work_dir,
                )

                # FORGE-232: ccx exiting 0 with no .frd at all is just as
                # empty a "result" as a .frd with node_count == 0 (parse_frd_
                # file below already raises for that case) -- both mean the
                # solver ran against an incomplete deck. Never report success
                # on either.
                frd_files = [f for f in solver_result.get("result_files", []) if f.endswith(".frd")]
                if not frd_files:
                    raise SolverError(
                        "CalculiX exited successfully but produced no .frd result file -- "
                        "nothing was actually solved (check the deck has a *STEP with real "
                        "loads/boundary conditions and *NODE FILE/*EL FILE output requests)."
                    )
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

                # Parse nodal temperature (NDTEMP) results from .frd (FORGE-232:
                # same "no result at all" cases as _execute_solver above).
                frd_files = [f for f in solver_result.get("result_files", []) if f.endswith(".frd")]
                if not frd_files:
                    raise SolverError(
                        "CalculiX exited successfully but produced no .frd result file -- "
                        "nothing was actually solved."
                    )
                parsed = parse_frd_file(frd_files[0])
                temperature = parsed.get("temperature", {})
                return {
                    "max_temperature": temperature.get("max", 0.0),
                    "min_temperature": temperature.get("min", 0.0),
                    "temperature_distribution": temperature.get("nodes", {}),
                    "solver_time": solver_result["solver_time_s"],
                    "result_files": solver_result["result_files"],
                }

            except Exception as exc:
                span.record_exception(exc)
                raise

    async def _validate_mesh_file(self, mesh_file: str, max_aspect_ratio: float) -> dict[str, Any]:
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
