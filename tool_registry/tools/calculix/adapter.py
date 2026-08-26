"""CalculiX FEA tool adapter -- MCP server for finite element analysis."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import structlog

from observability.tracing import get_tracer
from tool_registry.mcp_server.handlers import ResourceLimits, ToolManifest
from tool_registry.mcp_server.server import McpToolServer
from tool_registry.tools.calculix.config import CalculixConfig
from tool_registry.tools.calculix.deck import (
    DeckBuilder,
    DeckError,
    LoadCase,
    StepOptions,
    parse_load_cases,
)
from tool_registry.tools.calculix.materials import Material, resolve_material
from tool_registry.tools.calculix.mesh import Mesh, MeshParseError, parse_inp_mesh
from tool_registry.tools.calculix.mesh_quality import evaluate_mesh
from tool_registry.tools.calculix.result_parser import (
    extract_results,
    parse_dat_frequencies,
    parse_frd_file,
)
from tool_registry.tools.calculix.solver import run_fea as solver_run_fea

#: Analysis types accepted by ``calculix.run_fea``.
SUPPORTED_FEA_ANALYSES = ("static_stress", "modal")

#: Maps the tool's analysis names onto deck analysis names.
_DECK_ANALYSIS = {"static_stress": "static", "modal": "modal"}

#: A deck that already carries its own physics is solved as authored rather
#: than being regenerated. ``*STEP`` is the marker: no mesher emits one.
_COMPLETE_DECK_PATTERN = re.compile(r"^\s*\*STEP\b", re.IGNORECASE | re.MULTILINE)

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
                            "description": (
                                "Load case identifier. Names the analysis only; "
                                "pass load_cases to define the physics."
                            ),
                        },
                        "load_cases": {
                            "type": "array",
                            "description": (
                                "Load cases to solve. Each needs constraints and at "
                                "least one load; a complete input deck is generated "
                                "and solved per case."
                            ),
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "constraints": {
                                        "type": "array",
                                        "description": (
                                            "Displacement BCs: "
                                            '[{"region": {"face": "zmin"}, '
                                            '"kind": "fixed"}]'
                                        ),
                                    },
                                    "point_loads": {
                                        "type": "array",
                                        "description": (
                                            "Forces in N: "
                                            '[{"region": {"face": "zmax"}, '
                                            '"fz": -100.0}]'
                                        ),
                                    },
                                    "pressures": {
                                        "type": "array",
                                        "description": "Face pressures in MPa",
                                    },
                                    "gravity_mm_s2": {
                                        "type": "array",
                                        "description": (
                                            "Body acceleration [gx, gy, gz] in mm/s^2; "
                                            "earth gravity is 9810"
                                        ),
                                        "items": {"type": "number"},
                                    },
                                },
                            },
                        },
                        "analysis_type": {
                            "type": "string",
                            "enum": list(SUPPORTED_FEA_ANALYSES),
                            "description": "Type of analysis",
                        },
                        "material": {
                            "type": "string",
                            "description": (
                                "Material identifier, e.g. al6061_t6, steel_1018, "
                                "ti6al4v. Defaults to al6061_t6."
                            ),
                        },
                        "material_overrides": {
                            "type": "object",
                            "description": (
                                "Per-analysis property overrides in N-mm-s-tonne-K "
                                'units, e.g. {"yield_strength_mpa": 300.0}'
                            ),
                        },
                        "nlgeom": {
                            "type": "boolean",
                            "default": False,
                            "description": "Include geometric nonlinearity (large deformation)",
                        },
                        "eigenmodes": {
                            "type": "integer",
                            "default": 10,
                            "description": "Number of modes to extract (modal analysis)",
                        },
                    },
                    "required": ["mesh_file", "load_case", "analysis_type"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "max_von_mises": {
                            "type": "object",
                            "description": "Max von Mises stress (MPa) by load case",
                        },
                        "max_von_mises_mpa": {"type": "number"},
                        "max_displacement_mm": {"type": "number"},
                        "safety_factor": {
                            "type": "number",
                            "description": "Yield strength / governing stress",
                        },
                        "yield_strength_mpa": {"type": "number"},
                        "material": {"type": "string"},
                        "governing_load_case": {"type": "string"},
                        "load_cases": {"type": "array"},
                        "natural_frequencies_hz": {"type": "array"},
                        "solver_time": {"type": "number"},
                        "mesh_elements": {"type": "integer"},
                        "deck_files": {"type": "array"},
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
                        "boundary_conditions": {
                            "type": "object",
                            "description": (
                                "Thermal problem definition. Recognised keys: "
                                "thermal_boundaries (prescribed temperatures in K), "
                                "heat_fluxes (power in mW), convections (film "
                                "coefficient in mW/(mm^2*K) and sink temperature), "
                                "initial_temperature_k, time_increment_s, time_period_s."
                            ),
                            "properties": {
                                "thermal_boundaries": {"type": "array"},
                                "heat_fluxes": {"type": "array"},
                                "convections": {"type": "array"},
                                "initial_temperature_k": {"type": "number"},
                            },
                        },
                        "analysis_mode": {
                            "type": "string",
                            "enum": ["steady_state", "transient"],
                        },
                        "material": {"type": "string"},
                        "material_overrides": {"type": "object"},
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
                        "min_angle": {
                            "type": "number",
                            "default": 15.0,
                            "description": "Minimum acceptable dihedral angle in degrees",
                        },
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
                        "min_angle": {"type": "number"},
                        "min_scaled_jacobian": {"type": "number"},
                        "avg_quality": {"type": "number"},
                        "inverted_element_count": {"type": "integer"},
                        "sliver_element_count": {"type": "integer"},
                        "worst_elements": {"type": "array"},
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
        """Execute CalculiX FEA stress or modal analysis.

        When ``load_cases`` describes the physics, a complete input deck is
        generated per case -- material, section, boundary conditions, loads,
        analysis step and output requests -- and each is solved in turn. The
        reported safety factor is the worst across every case.

        When only a legacy ``load_case`` name is given, the mesh file is solved
        as authored, which is correct for a hand-written deck and warned about
        for a geometry-only mesh.
        """
        mesh_file = arguments.get("mesh_file", "")
        load_case = arguments.get("load_case", "")
        load_cases = arguments.get("load_cases")
        analysis_type = arguments.get("analysis_type", "static_stress")

        if not mesh_file:
            raise ValueError("mesh_file is required")
        if not load_case and not load_cases:
            raise ValueError("load_case is required")
        if analysis_type not in SUPPORTED_FEA_ANALYSES:
            raise ValueError(f"Unsupported analysis type: {analysis_type}")

        logger.info(
            "Running FEA analysis",
            mesh_file=mesh_file,
            load_case=load_case,
            analysis_type=analysis_type,
            structured_cases=bool(load_cases),
        )

        material = resolve_material(
            arguments.get("material"),
            arguments.get("material_overrides"),
        )

        cases = parse_load_cases(load_cases, default_name=str(load_case) or "load_case")

        if not cases:
            return await self._solve_authored_deck(
                mesh_file, analysis_type, material, str(load_case)
            )

        options = StepOptions(
            analysis=_DECK_ANALYSIS[analysis_type],
            nlgeom=bool(arguments.get("nlgeom", False)),
            eigenmodes=int(arguments.get("eigenmodes", 10)),
        )
        return await self._solve_generated_decks(mesh_file, cases, options, material)

    async def _solve_authored_deck(
        self,
        mesh_file: str,
        analysis_type: str,
        material: Material,
        load_case: str,
    ) -> dict[str, Any]:
        """Solve a deck the caller supplied, without generating physics.

        A geometry-only mesh reaching this path is the historical silent
        failure: CalculiX exits zero having written a result file with no
        stress in it. The warning names the argument that fixes it.
        """
        if not _is_complete_deck(mesh_file):
            logger.warning(
                "Solving a mesh that carries no analysis step",
                mesh_file=mesh_file,
                load_case=load_case,
                remedy=(
                    "Pass 'load_cases' with constraints and loads so a complete "
                    "deck can be generated; a geometry-only mesh produces no stress."
                ),
            )

        result = await self._execute_solver(mesh_file, analysis_type)
        return self._decorate_result(result, material)

    async def _solve_generated_decks(
        self,
        mesh_file: str,
        cases: list[LoadCase],
        options: StepOptions,
        material: Material,
    ) -> dict[str, Any]:
        """Generate and solve one deck per load case, then aggregate."""
        mesh = _load_mesh(mesh_file)
        mesh_path = Path(mesh_file).resolve()
        deck_dir = self._deck_dir(mesh_path)
        builder = DeckBuilder(mesh, material, mesh_include=str(mesh_path))

        per_case: list[dict[str, Any]] = []
        stress_by_case: dict[str, float] = {}
        max_displacement = 0.0
        total_solver_time = 0.0
        frequencies: list[dict[str, float]] = []
        result_files: list[str] = []
        deck_files: list[str] = []

        used_slugs: set[str] = set()

        for case in cases:
            slug = _unique_slug(case.name, used_slugs)
            deck_path = deck_dir / f"{mesh_path.stem}_{slug}.inp"
            builder.write(case, deck_path, options)
            deck_files.append(str(deck_path))

            solver_result = await self._execute_solver(
                str(deck_path), "modal" if options.analysis == "modal" else "static_stress"
            )

            total_solver_time += float(solver_result.get("solver_time", 0.0) or 0.0)
            result_files.extend(solver_result.get("result_files", []))

            stress = solver_result.get("stress", {}) or {}
            displacement = solver_result.get("displacement", {}) or {}
            case_stress = float(stress.get("max", 0.0) or 0.0)
            case_displacement = float(displacement.get("max", 0.0) or 0.0)

            stress_by_case[case.name] = case_stress
            max_displacement = max(max_displacement, case_displacement)

            case_frequencies = _read_frequencies(solver_result, options.analysis)
            frequencies.extend(case_frequencies)

            per_case.append(
                {
                    "name": case.name,
                    "deck_file": str(deck_path),
                    "max_von_mises_mpa": round(case_stress, 4),
                    "max_displacement_mm": round(case_displacement, 6),
                    "safety_factor": _round_safety_factor(material.safety_factor(case_stress)),
                    "solver_time": solver_result.get("solver_time", 0.0),
                    "natural_frequencies_hz": [f["frequency_hz"] for f in case_frequencies],
                }
            )

        governing_stress = max(stress_by_case.values(), default=0.0)

        result: dict[str, Any] = {
            "max_von_mises": {name: round(v, 4) for name, v in stress_by_case.items()},
            "max_von_mises_mpa": round(governing_stress, 4),
            "max_displacement_mm": round(max_displacement, 6),
            "solver_time": round(total_solver_time, 3),
            "mesh_elements": mesh.element_count,
            "mesh_nodes": mesh.node_count,
            "analysis_type": options.analysis,
            "load_cases": per_case,
            "governing_load_case": max(stress_by_case, key=lambda k: stress_by_case[k])
            if stress_by_case
            else None,
            "result_files": result_files,
            "deck_files": deck_files,
        }

        if frequencies:
            result["natural_frequencies_hz"] = [f["frequency_hz"] for f in frequencies]
            result["modes"] = frequencies

        return self._decorate_result(result, material, governing_stress)

    def _deck_dir(self, mesh_path: Path) -> Path:
        """Directory to write generated decks into.

        The solver is invoked with ``ccx -i <job>`` from ``config.work_dir``, so
        a deck written anywhere else is invisible to it. The configured
        directory wins; it falls back to the mesh's own directory only when it
        cannot be created (a read-only or unset work dir).
        """
        configured = self.config.work_dir
        if configured:
            candidate = Path(configured)
            try:
                candidate.mkdir(parents=True, exist_ok=True)
                return candidate
            except OSError as exc:
                logger.warning(
                    "Configured work_dir is unusable; writing decks beside the mesh",
                    work_dir=configured,
                    error=str(exc),
                )
        return mesh_path.parent

    def _decorate_result(
        self,
        result: dict[str, Any],
        material: Material,
        governing_stress: float | None = None,
    ) -> dict[str, Any]:
        """Attach material identity and the derived safety factor.

        The safety factor was previously absent from every response, so callers
        reading ``safety_factor`` silently defaulted it to zero. It is derived
        here from the resolved material's yield strength and the governing
        stress.
        """
        if governing_stress is None:
            reported = result.get("max_von_mises", {})
            if isinstance(reported, dict) and reported:
                governing_stress = max(float(v) for v in reported.values())
            else:
                governing_stress = float(result.get("max_von_mises_mpa", 0.0) or 0.0)

        result["material"] = material.key
        result["material_name"] = material.name
        result["yield_strength_mpa"] = material.yield_strength_mpa
        result["safety_factor"] = _round_safety_factor(material.safety_factor(governing_stress))
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
        """Execute CalculiX thermal analysis.

        ``boundary_conditions`` describes the thermal problem: prescribed
        temperatures, heat fluxes, and convective film conditions. These are
        turned into a real ``*HEAT TRANSFER`` deck; previously they were
        validated as non-empty and then discarded, so the solve carried no
        thermal load at all.
        """
        mesh_file = arguments.get("mesh_file", "")
        boundary_conditions = arguments.get("boundary_conditions", {})
        analysis_mode = arguments.get("analysis_mode", "steady_state")

        if not mesh_file:
            raise ValueError("mesh_file is required")
        if not boundary_conditions:
            raise ValueError("boundary_conditions is required")
        if analysis_mode not in ("steady_state", "transient"):
            raise ValueError(f"Unsupported analysis mode: {analysis_mode}")

        logger.info("Running thermal analysis", mesh_file=mesh_file, mode=analysis_mode)

        material = resolve_material(
            arguments.get("material"),
            arguments.get("material_overrides"),
        )

        case = _thermal_load_case(boundary_conditions)
        if case is None:
            return await self._execute_thermal_solver(mesh_file, boundary_conditions, analysis_mode)

        options = StepOptions(
            analysis="thermal",
            steady_state=analysis_mode == "steady_state",
            time_increment=float(boundary_conditions.get("time_increment_s", 1.0) or 1.0),
            time_period=float(boundary_conditions.get("time_period_s", 1.0) or 1.0),
            initial_temperature_k=float(boundary_conditions.get("initial_temperature_k", 293.15)),
        )

        mesh = _load_mesh(mesh_file)
        mesh_path = Path(mesh_file).resolve()
        deck_path = self._deck_dir(mesh_path) / f"{mesh_path.stem}_thermal.inp"
        DeckBuilder(mesh, material, mesh_include=str(mesh_path)).write(case, deck_path, options)

        result = await self._execute_thermal_solver(
            str(deck_path), boundary_conditions, analysis_mode
        )
        result["deck_files"] = [str(deck_path)]
        result["analysis_mode"] = analysis_mode
        result["material"] = material.key
        result["mesh_elements"] = mesh.element_count
        result["mesh_nodes"] = mesh.node_count
        return result

    async def validate_mesh(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Validate mesh quality without running a full solve.

        Measures element geometry -- aspect ratio, dihedral angles, scaled
        Jacobian, signed volume -- so slivers and inverted elements are caught
        before they silently corrupt a stress result.
        """
        mesh_file = arguments.get("mesh_file", "")
        max_aspect_ratio = arguments.get("max_aspect_ratio", 10.0)
        min_angle = arguments.get("min_angle", 15.0)

        if not mesh_file:
            raise ValueError("mesh_file is required")

        logger.info("Validating mesh", mesh_file=mesh_file)

        return await self._validate_mesh_file(mesh_file, max_aspect_ratio, min_angle)

    async def _execute_solver(self, mesh_file: str, analysis_type: str) -> dict[str, Any]:
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
                frd_files = [f for f in solver_result.get("result_files", []) if f.endswith(".frd")]
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

                # Parse nodal temperature (NDTEMP) results from .frd if available
                frd_files = [f for f in solver_result.get("result_files", []) if f.endswith(".frd")]
                if frd_files:
                    parsed = parse_frd_file(frd_files[0])
                    temperature = parsed.get("temperature", {})
                    return {
                        "max_temperature": temperature.get("max", 0.0),
                        "min_temperature": temperature.get("min", 0.0),
                        "temperature_distribution": temperature.get("nodes", {}),
                        "solver_time": solver_result["solver_time_s"],
                        "result_files": solver_result["result_files"],
                    }

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
        self,
        mesh_file: str,
        max_aspect_ratio: float,
        min_angle: float = 15.0,
    ) -> dict[str, Any]:
        """Measure mesh quality from element geometry.

        Counting ``*NODE`` and ``*ELEMENT`` lines cannot distinguish a
        well-shaped mesh from one full of slivers, and multi-line connectivity
        records make the line count wrong as well. This parses the mesh and
        measures every solid element.

        This method is designed to be easily mockable in tests.
        """
        with tracer.start_as_current_span("calculix.validate_mesh") as span:
            span.set_attribute("calculix.mesh_file", mesh_file)
            span.set_attribute("calculix.max_aspect_ratio", max_aspect_ratio)

            try:
                mesh = _load_mesh(mesh_file)
                report = evaluate_mesh(
                    mesh,
                    max_aspect_ratio=max_aspect_ratio,
                    min_angle_deg=min_angle,
                )
            except Exception as exc:
                span.record_exception(exc)
                raise

            span.set_attribute("calculix.mesh_valid", report.valid)
            span.set_attribute("calculix.element_count", report.element_count)
            return report.to_dict()


def _is_complete_deck(mesh_file: str) -> bool:
    """Whether a file already contains its own analysis step.

    Returns ``False`` when the file cannot be read, so an unreadable path falls
    through to the solver's own error rather than being misreported here.
    """
    try:
        content = Path(mesh_file).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return bool(_COMPLETE_DECK_PATTERN.search(content))


def _load_mesh(mesh_file: str) -> Mesh:
    """Parse a mesh, translating parse failures into actionable errors."""
    try:
        return parse_inp_mesh(mesh_file)
    except MeshParseError as exc:
        raise DeckError(f"Could not read '{mesh_file}' as a finite element mesh: {exc}") from exc


def _unique_slug(name: str, used: set[str]) -> str:
    """Slugify a load case name, disambiguating against names already used.

    Two cases whose names differ only past the truncation limit would otherwise
    resolve to the same deck filename, and the second solve would overwrite and
    silently be reported for both.
    """
    slug = _slugify(name)
    candidate = slug
    suffix = 2
    while candidate in used:
        candidate = f"{slug}_{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate


def _slugify(name: str) -> str:
    """Reduce a load case name to a filename-safe token.

    CalculiX derives its job name from the deck filename, so the token must
    survive a round trip through the filesystem and the solver's argument
    parsing.
    """
    slug = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").lower()
    return slug[:48] or "case"


def _round_safety_factor(value: float) -> float:
    """Round a safety factor, preserving an unbounded margin as-is."""
    return value if value == float("inf") else round(value, 4)


def _read_frequencies(solver_result: dict[str, Any], analysis: str) -> list[dict[str, float]]:
    """Pull eigenfrequencies out of a modal solve's ``.dat`` file.

    Mode shapes go to the ``.frd`` but eigenvalues only to the ``.dat``, so a
    modal analysis read from the ``.frd`` alone reports no frequencies.
    """
    if analysis != "modal":
        return []

    dat_files = [f for f in solver_result.get("result_files", []) if f.endswith(".dat")]
    if not dat_files:
        logger.warning("Modal analysis produced no .dat file; no frequencies available")
        return []

    try:
        return parse_dat_frequencies(dat_files[0])
    except Exception as exc:
        logger.warning("Failed to parse eigenfrequencies", error=str(exc))
        return []


def _thermal_load_case(boundary_conditions: dict[str, Any]) -> LoadCase | None:
    """Build a thermal load case from the ``boundary_conditions`` payload.

    Recognised keys are ``thermal_boundaries`` (or ``fixed_temperatures``),
    ``heat_fluxes`` and ``convections``. A payload carrying none of them -- the
    legacy ``{"ambient_temp": ..., "heat_flux": ...}`` shape, which names no
    region to apply anything to -- returns ``None`` so the caller can fall back
    to solving the file as authored.
    """
    payload: dict[str, Any] = {"name": "thermal"}

    boundaries = boundary_conditions.get("thermal_boundaries") or boundary_conditions.get(
        "fixed_temperatures"
    )
    if boundaries:
        payload["thermal_boundaries"] = boundaries
    if boundary_conditions.get("heat_fluxes"):
        payload["heat_fluxes"] = boundary_conditions["heat_fluxes"]
    if boundary_conditions.get("convections"):
        payload["convections"] = boundary_conditions["convections"]

    if len(payload) == 1:
        logger.warning(
            "Thermal boundary conditions name no region; solving the file as authored",
            keys=sorted(boundary_conditions),
            remedy=(
                "Pass 'thermal_boundaries', 'heat_fluxes' or 'convections' with a "
                "region so a heat transfer deck can be generated."
            ),
        )
        return None

    try:
        return LoadCase.model_validate(payload)
    except Exception as exc:
        raise DeckError(f"Invalid thermal boundary conditions: {exc}") from exc
