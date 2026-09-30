"""CalculiX FEA tool adapter -- MCP server for finite element analysis."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

from observability.tracing import get_tracer
from tool_registry.mcp_server.handlers import ResourceLimits, ToolManifest
from tool_registry.mcp_server.server import McpToolServer
from tool_registry.tools.cadquery.materials import (
    resolve_density_kg_m3,
    resolve_elastic_properties,
    resolve_thermal_conductivity_w_mk,
)
from tool_registry.tools.calculix.accuracy import (
    check_mesh_convergence,
    cross_check_cantilever_bending,
    cross_check_cantilever_frequency,
    cross_check_thermal_steady_state,
)
from tool_registry.tools.calculix.config import CalculixConfig
from tool_registry.tools.calculix.deck_builder import (
    build_modal_deck,
    build_static_stress_deck,
    build_thermal_deck,
)
from tool_registry.tools.calculix.inp_mesh import MeshData, parse_mesh_inp
from tool_registry.tools.calculix.result_parser import (
    extract_results,
    parse_frd_file,
    parse_frequencies_dat,
)
from tool_registry.tools.calculix.solver import SolverError
from tool_registry.tools.calculix.solver import run_fea as solver_run_fea
from tool_registry.tools.calculix.statics import (
    ChainJoint,
    ChainLink,
    compute_joint_loads,
    worst_joint_load,
)

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

# FORGE-239: a fixed_node_set that spans this much of the part's own extent
# along its LONGEST axis (its "length") is very likely the wrong face, not a
# legitimately large one -- reported live: a cantilever's fixed end was
# meant to be the x=0 face, but the model picked a gmsh-named group
# ("Surface1") that turned out to be the entire long side (x spans the full
# 0..360mm part length, not just the x=0 end), clamping the whole beam and
# understating stress ~10x. Checked ONLY on the longest axis, not all three
# independently: a real, intentionally-large fixed face (e.g. a full end
# cap) legitimately spans ~100% of the part's OTHER two (cross-sectional)
# axes -- that's not suspicious, and checking every axis independently
# false-positived on exactly that shape during this fix's own testing.
_FIXED_SET_SPAN_WARNING_THRESHOLD = 0.5


def _fixed_node_set_span_warning(
    mesh: MeshData, fixed_node_set: str, *, role: str = "fixed_node_set"
) -> str | None:
    """None if fixed_node_set's bbox looks like a real face; else a warning
    naming how much of the part's longest axis it suspiciously spans.

    FORGE-282: ``role`` lets callers outside the static/modal path (e.g.
    thermal's ``heat_source_node_set``/``sink_node_set``) reuse this same
    check under their own node-set's real name in the message -- caught
    live: a real thermal analysis picked a node set that turned out to be
    a full-length SIDE face (not the intended small end-cap) as its
    fixed-temperature sink, putting the "sink" immediately adjacent (in a
    cross-axis direction) to the heat source along its entire length and
    collapsing the effective conduction path from ~360mm to a few mm --
    reporting a peak temperature 5.5C above ambient instead of the ~55C a
    correct end-cap-to-end-cap path actually produces. No error, no
    warning, just a confidently wrong (and much too comfortable) number.
    """
    try:
        fixed_ids = mesh.node_ids_for_elset(fixed_node_set)
        fixed_bbox = mesh.bounding_box_for_nodes(fixed_ids)
        whole_bbox = mesh.bounding_box_for_nodes(list(mesh.nodes))
    except (KeyError, ValueError):
        return None  # let the caller's own error paths report the real problem

    extents = {
        axis: whole_bbox[f"max_{axis}"] - whole_bbox[f"min_{axis}"] for axis in ("x", "y", "z")
    }
    axis = max(extents, key=lambda a: extents[a])
    whole_extent = extents[axis]
    if whole_extent <= 1e-9:
        return None  # a degenerate (point-like) mesh -- nothing to compare against
    fixed_extent = fixed_bbox[f"max_{axis}"] - fixed_bbox[f"min_{axis}"]
    ratio = fixed_extent / whole_extent
    if ratio > _FIXED_SET_SPAN_WARNING_THRESHOLD:
        return (
            f"{role} {fixed_node_set!r} spans {ratio:.0%} of the part's length "
            f"(its longest axis, {axis}: {fixed_extent:.3g}mm of {whole_extent:.3g}mm) -- "
            "this usually means the wrong face was picked (e.g. a whole side instead "
            "of just one end), which silently misrepresents the boundary condition and "
            "produces a confidently wrong result. Use freecad.generate_mesh's own 'faces' "
            "table (bbox/centroid per named face) to pick the intended one by its "
            "real coordinates before re-running."
        )
    return None


class CalculixServer(McpToolServer):
    """CalculiX FEA tool adapter.

    Provides nine tools:
    - calculix.run_fea: static-stress or modal (FORGE-281) FEA analysis
    - calculix.extract_results: parse existing .frd result files
    - calculix.run_thermal: steady-state conduction thermal analysis (FORGE-282)
    - calculix.validate_mesh: validate mesh quality
    - calculix.cross_check_cantilever_beam: hand-calc stress cross-check
    - calculix.cross_check_cantilever_frequency: hand-calc frequency cross-check
    - calculix.cross_check_thermal_steady_state: hand-calc peak-temperature cross-check (FORGE-282)
    - calculix.check_mesh_convergence: compare results across element sizes
    - calculix.compute_joint_loads: quasi-static joint reaction loads (FORGE-283)
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
                                "Type of analysis. FORGE-234/FORGE-281: material/"
                                "fixed_node_set build a complete, solvable deck for both "
                                "'static_stress' and 'modal'; load_node_set/load_force_n "
                                "are static_stress-only (a modal solve is an eigenvalue "
                                "problem, it has no applied load) and num_modes is "
                                "modal-only."
                            ),
                        },
                        "material": {
                            "type": "object",
                            "description": (
                                "Required for both 'static_stress' and 'modal'. Either "
                                "{'name': <materials.py name, e.g. 'steel'/"
                                "'aluminum_6061'>} or explicit {'youngs_modulus_mpa': ..., "
                                "'poissons_ratio': ...}. 'modal' additionally needs a real "
                                "density (a wrong one silently reports a confidently wrong "
                                "frequency), which this codebase only has a lookup table "
                                "for by name -- 'name' is required for 'modal' even when "
                                "explicit elastic properties are also given. MPa (N/mm^2), "
                                "NOT Pa -- mesh "
                                "coordinates are in millimeters, and mixing unit systems "
                                "silently understates stiffness by 1e6."
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
                                "Required for both 'static_stress' and 'modal'. Element "
                                "set name from freecad.generate_mesh's own mesh (e.g. "
                                "'Surface1', gmsh's per-STEP-face group) to fully "
                                "constrain (all 3 translational DOFs) -- use "
                                "generate_mesh's own 'faces' response to identify which "
                                "named face is which by its bounding box."
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
                        "num_modes": {
                            "type": "integer",
                            "default": 3,
                            "description": (
                                "'modal' only. Number of natural frequencies/mode shapes "
                                "to extract, lowest first (CalculiX's own Lanczos "
                                "default eigensolver)."
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
                            "description": "Max stress by region (static_stress only).",
                        },
                        "frequencies_hz": {
                            "type": "array",
                            "items": {"type": "number"},
                            "description": (
                                "modal only. Natural frequencies in Hz, lowest mode first."
                            ),
                        },
                        "solver_time": {"type": "number"},
                        "mesh_elements": {"type": "integer"},
                        "frd_path": {
                            "type": "string",
                            "description": (
                                "FORGE-239: the exact .frd result file to pass to "
                                "calculix.extract_results -- note it is "
                                "'<mesh_stem>_solved.frd', NOT '<mesh_stem>.frd'."
                            ),
                        },
                        "warnings": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "FORGE-239: present only when something about this "
                                "run looks suspect (e.g. fixed_node_set spans most "
                                "of the part along one axis, which usually means "
                                "the wrong face was picked). The solve still "
                                "completed -- but don't record this result as "
                                "trustworthy evidence without addressing the "
                                "warning first."
                            ),
                        },
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
                description=(
                    "Steady-state conduction thermal analysis using CalculiX (FORGE-282). "
                    "Models a real heat source (e.g. a BOM component's known power "
                    "dissipation -- this adapter has no Twin access, so the caller "
                    "resolves the component's specifications.powerDissipationW itself "
                    "and passes the raw wattage here) conducting through the part to a "
                    "fixed-temperature sink (e.g. a chassis/heatsink mount held near-"
                    "ambient). Deliberately conduction-only -- no convective (*FILM) "
                    "boundary to open air, since that needs exposed element-face "
                    "geometry this mesh-handling codebase doesn't derive anywhere yet; "
                    "see calculix.cross_check_thermal_steady_state for a hand-calc "
                    "sanity check against the exact same fixed-sink model."
                ),
                capability="thermal_analysis",
                input_schema={
                    "type": "object",
                    "properties": {
                        "mesh_file": {"type": "string", "description": _MESH_FILE_DESCRIPTION},
                        "analysis_mode": {
                            "type": "string",
                            "enum": ["steady_state", "transient"],
                            "description": (
                                "Only 'steady_state' is implemented. 'transient' is "
                                "accepted in the schema for forward compatibility but "
                                "raises -- there is no time-stepping deck builder yet."
                            ),
                        },
                        "material": {
                            "type": "object",
                            "description": (
                                "Required. {'name': <materials.py name, e.g. 'steel'/"
                                "'aluminum_6061'>} or explicit "
                                "{'thermal_conductivity_w_mk': ...} (SI W/(m*K), NOT "
                                "the mesh's mm-consistent W/(mm*K) -- converted "
                                "internally)."
                            ),
                            "properties": {
                                "name": {"type": "string"},
                                "thermal_conductivity_w_mk": {"type": "number"},
                            },
                        },
                        "heat_source_node_set": {
                            "type": "string",
                            "description": (
                                "Element set name (from freecad.generate_mesh's own "
                                "mesh) where power_dissipation_w is applied, e.g. the "
                                "component's mounting face."
                            ),
                        },
                        "power_dissipation_w": {
                            "type": "number",
                            "description": (
                                "TOTAL heat generation in Watts, distributed evenly "
                                "across heat_source_node_set's nodes."
                            ),
                        },
                        "sink_node_set": {
                            "type": "string",
                            "description": (
                                "Element set name held at a fixed sink_temp_c (e.g. a "
                                "chassis/heatsink mounting face) -- the boundary heat "
                                "conducts away through."
                            ),
                        },
                        "sink_temp_c": {
                            "type": "number",
                            "description": "Fixed temperature (Celsius) of sink_node_set.",
                        },
                    },
                    "required": [
                        "mesh_file",
                        "material",
                        "heat_source_node_set",
                        "power_dissipation_w",
                        "sink_node_set",
                        "sink_temp_c",
                    ],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "max_temperature_c": {"type": "number"},
                        "min_temperature_c": {"type": "number"},
                        "solver_time": {"type": "number"},
                        "frd_path": {"type": "string"},
                        "warnings": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Present only when heat_source_node_set or "
                                "sink_node_set looks suspect (spans most of the "
                                "part along one axis, usually the wrong face). "
                                "The solve still completed -- but don't trust the "
                                "reported temperature without addressing the "
                                "warning first."
                            ),
                        },
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
                description=(
                    "Parse existing CalculiX .frd result files into structured JSON. "
                    "FORGE-280: the stress block's own 'accuracy' field auto-flags a "
                    "suspicious result (max stress disproportionate to the rest of "
                    "the field, usually a point-load/BC concentration artifact) — "
                    "check it before trusting max_von_mises for a safety-factor call. "
                    "For a second opinion, calculix.cross_check_cantilever_beam (a "
                    "hand calc, for the textbook case) and "
                    "calculix.check_mesh_convergence (run at 2+ element sizes and "
                    "compare) are separate tools."
                ),
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
                        "stress": {
                            "type": "object",
                            "description": (
                                "nodes, max, min, avg, and 'accuracy' "
                                "({suspicious, reason, max_to_median_ratio})."
                            ),
                        },
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

        self.register_tool(
            manifest=ToolManifest(
                tool_id="calculix.cross_check_cantilever_beam",
                adapter_id="calculix",
                name="Cross-Check Cantilever Beam",
                description=(
                    "Euler-Bernoulli hand calc for a rectangular cantilever with a "
                    "tip point load (sigma = M*c/I) — FORGE-280. Compares against an "
                    "FEA max stress within a tolerance, for exactly the textbook case "
                    "a human caught FORGE-239's bad fixed_node_set with manually. "
                    "Only valid for this one loading case (fixed-free cantilever, "
                    "rectangular cross-section, tip load) — not a general beam solver."
                ),
                capability="accuracy_check",
                input_schema={
                    "type": "object",
                    "properties": {
                        "length_mm": {
                            "type": "number",
                            "description": "Distance from the fixed end to the tip load, mm.",
                        },
                        "width_mm": {
                            "type": "number",
                            "description": "Cross-section width (bending-neutral direction), mm.",
                        },
                        "height_mm": {
                            "type": "number",
                            "description": "Cross-section height (in the bending direction), mm.",
                        },
                        "force_n": {
                            "type": "number",
                            "description": "Tip point load, Newtons (perpendicular to beam axis).",
                        },
                        "fea_max_stress_mpa": {
                            "type": "number",
                            "description": "The FEA run's own max von Mises stress, MPa, to check.",
                        },
                        "tolerance_pct": {
                            "type": "number",
                            "default": 20.0,
                            "description": (
                                "Max allowed percent difference between the hand calc "
                                "and the FEA number before flagging a mismatch."
                            ),
                        },
                    },
                    "required": [
                        "length_mm",
                        "width_mm",
                        "height_mm",
                        "force_n",
                        "fea_max_stress_mpa",
                    ],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "hand_calc_stress_mpa": {"type": "number"},
                        "fea_max_stress_mpa": {"type": "number"},
                        "percent_difference": {"type": "number"},
                        "tolerance_pct": {"type": "number"},
                        "within_tolerance": {"type": "boolean"},
                    },
                },
                phase=1,
                resource_limits=ResourceLimits(max_memory_mb=64, max_cpu_seconds=5),
            ),
            handler=self.handle_cross_check_cantilever_beam,
        )

        self.register_tool(
            manifest=ToolManifest(
                tool_id="calculix.cross_check_cantilever_frequency",
                adapter_id="calculix",
                name="Cross-Check Cantilever Frequency",
                description=(
                    "Closed-form first-bending-mode natural frequency for a uniform "
                    "rectangular cantilever, tip-free (FORGE-281) -- the modal sibling "
                    "of calculix.cross_check_cantilever_beam, same one-textbook-case "
                    "discipline (fixed-free, rectangular cross-section). Compares "
                    "against a modal FEA run's own first mode within a tolerance."
                ),
                capability="accuracy_check",
                input_schema={
                    "type": "object",
                    "properties": {
                        "length_mm": {
                            "type": "number",
                            "description": "Distance from the fixed end to the free tip, mm.",
                        },
                        "width_mm": {
                            "type": "number",
                            "description": "Cross-section width (bending-neutral direction), mm.",
                        },
                        "height_mm": {
                            "type": "number",
                            "description": "Cross-section height (in the bending direction), mm.",
                        },
                        "density_kg_m3": {
                            "type": "number",
                            "description": "Material density, kg/m^3.",
                        },
                        "youngs_modulus_mpa": {
                            "type": "number",
                            "description": "Material Young's modulus, MPa (N/mm^2).",
                        },
                        "fea_first_mode_hz": {
                            "type": "number",
                            "description": (
                                "The modal FEA run's own first (lowest) natural "
                                "frequency, Hz, to check."
                            ),
                        },
                        "tolerance_pct": {
                            "type": "number",
                            "default": 20.0,
                            "description": (
                                "Max allowed percent difference between the hand calc "
                                "and the FEA number before flagging a mismatch."
                            ),
                        },
                    },
                    "required": [
                        "length_mm",
                        "width_mm",
                        "height_mm",
                        "density_kg_m3",
                        "youngs_modulus_mpa",
                        "fea_first_mode_hz",
                    ],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "hand_calc_first_mode_hz": {"type": "number"},
                        "fea_first_mode_hz": {"type": "number"},
                        "percent_difference": {"type": "number"},
                        "tolerance_pct": {"type": "number"},
                        "within_tolerance": {"type": "boolean"},
                    },
                },
                phase=1,
                resource_limits=ResourceLimits(max_memory_mb=64, max_cpu_seconds=5),
            ),
            handler=self.handle_cross_check_cantilever_frequency,
        )

        self.register_tool(
            manifest=ToolManifest(
                tool_id="calculix.cross_check_thermal_steady_state",
                adapter_id="calculix",
                name="Cross-Check Thermal Steady State",
                description=(
                    "1D steady-state conduction hand calc (FORGE-282) -- the thermal "
                    "sibling of calculix.cross_check_cantilever_beam, same one-"
                    "textbook-case discipline, for the exact fixed-temperature-sink "
                    "model calculix.run_thermal solves (no convection). Compares "
                    "against a thermal FEA run's own peak temperature within a "
                    "tolerance."
                ),
                capability="accuracy_check",
                input_schema={
                    "type": "object",
                    "properties": {
                        "conduction_length_mm": {
                            "type": "number",
                            "description": "Straight-line distance, heat source to sink, mm.",
                        },
                        "cross_section_area_mm2": {
                            "type": "number",
                            "description": "Cross-sectional area of the conduction path, mm^2.",
                        },
                        "thermal_conductivity_w_mk": {
                            "type": "number",
                            "description": "Material thermal conductivity, SI W/(m*K).",
                        },
                        "power_dissipation_w": {
                            "type": "number",
                            "description": "Total heat generation, Watts.",
                        },
                        "sink_temp_c": {
                            "type": "number",
                            "description": "Fixed sink temperature, Celsius.",
                        },
                        "fea_peak_temp_c": {
                            "type": "number",
                            "description": (
                                "The thermal FEA run's own max nodal temperature, "
                                "Celsius, to check."
                            ),
                        },
                        "tolerance_pct": {
                            "type": "number",
                            "default": 20.0,
                            "description": (
                                "Max allowed percent difference between the hand calc "
                                "and the FEA number before flagging a mismatch."
                            ),
                        },
                    },
                    "required": [
                        "conduction_length_mm",
                        "cross_section_area_mm2",
                        "thermal_conductivity_w_mk",
                        "power_dissipation_w",
                        "sink_temp_c",
                        "fea_peak_temp_c",
                    ],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "hand_calc_peak_temp_c": {"type": "number"},
                        "fea_peak_temp_c": {"type": "number"},
                        "percent_difference": {"type": "number"},
                        "tolerance_pct": {"type": "number"},
                        "within_tolerance": {"type": "boolean"},
                    },
                },
                phase=1,
                resource_limits=ResourceLimits(max_memory_mb=64, max_cpu_seconds=5),
            ),
            handler=self.handle_cross_check_thermal_steady_state,
        )

        self.register_tool(
            manifest=ToolManifest(
                tool_id="calculix.check_mesh_convergence",
                adapter_id="calculix",
                name="Check Mesh Convergence",
                description=(
                    "Whether max stress has stopped changing meaningfully across "
                    "element sizes already run (FORGE-280) — pass the "
                    "max_von_mises_mpa this tool's own calculix.run_fea/"
                    "extract_results produced at each of 2+ element sizes; this does "
                    "NOT run the sweep itself, only compares results you already have."
                ),
                capability="accuracy_check",
                input_schema={
                    "type": "object",
                    "properties": {
                        "points": {
                            "type": "array",
                            "minItems": 2,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "element_size_mm": {"type": "number"},
                                    "max_von_mises_mpa": {"type": "number"},
                                },
                                "required": ["element_size_mm", "max_von_mises_mpa"],
                            },
                            "description": "One entry per element size already run, at least 2.",
                        },
                        "tolerance_pct": {
                            "type": "number",
                            "default": 5.0,
                            "description": (
                                "Max allowed percent change in max stress between "
                                "the two finest sizes before calling it converged."
                            ),
                        },
                    },
                    "required": ["points"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "points": {"type": "array"},
                        "changes": {"type": "array"},
                        "converged": {"type": "boolean"},
                        "recommendation": {"type": "string"},
                    },
                },
                phase=1,
                resource_limits=ResourceLimits(max_memory_mb=64, max_cpu_seconds=5),
            ),
            handler=self.handle_check_mesh_convergence,
        )

        self.register_tool(
            manifest=ToolManifest(
                tool_id="calculix.compute_joint_loads",
                adapter_id="calculix",
                name="Compute Joint Loads",
                description=(
                    "Quasi-static reaction force/moment at every joint of a posed "
                    "serial robot-arm chain, from gravity alone (FORGE-283). Given "
                    "each link's world-frame center of mass and mass at a chosen "
                    "pose, plus each joint's world-frame position at that same pose, "
                    "returns the total supported weight and bending/torsional moment "
                    "each joint must react to hold the pose static -- the classic "
                    "'arm fully extended holding a payload' load case that dominates "
                    "structural sizing. Deliberately quasi-static: no velocity/"
                    "acceleration terms from an actual trajectory, no friction, no "
                    "actuator torque-speed curves -- a full dynamic worst-case-over-"
                    "motion analysis needs a real multibody dynamics engine, which "
                    "this codebase's Gazebo/Isaac adapters don't yet expose (no "
                    "force/torque output from either today). The worst-loaded "
                    "joint's reaction_force_n can be fed directly into "
                    "calculix.run_fea's load_force_n; a reaction_moment_n_mm can be "
                    "applied as a force couple (two equal-and-opposite point loads "
                    "separated by a lever arm) using that same static_stress path."
                ),
                capability="load_analysis",
                input_schema={
                    "type": "object",
                    "properties": {
                        "links": {
                            "type": "array",
                            "minItems": 1,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "com_world_mm": {
                                        "type": "array",
                                        "items": {"type": "number"},
                                        "minItems": 3,
                                        "maxItems": 3,
                                    },
                                    "mass_kg": {"type": "number"},
                                },
                                "required": ["name", "com_world_mm", "mass_kg"],
                            },
                            "description": (
                                "Base-to-tip. links[i] is the link immediately "
                                "outboard of joints[i] -- same length as joints, "
                                "paired by index."
                            ),
                        },
                        "joints": {
                            "type": "array",
                            "minItems": 1,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "position_world_mm": {
                                        "type": "array",
                                        "items": {"type": "number"},
                                        "minItems": 3,
                                        "maxItems": 3,
                                    },
                                },
                                "required": ["name", "position_world_mm"],
                            },
                            "description": "Base-to-tip, same length as links.",
                        },
                        "payload_mass_kg": {
                            "type": "number",
                            "default": 0.0,
                            "description": "Optional end-effector payload mass, kg.",
                        },
                        "payload_position_world_mm": {
                            "type": "array",
                            "items": {"type": "number"},
                            "minItems": 3,
                            "maxItems": 3,
                            "description": "Required if payload_mass_kg > 0.",
                        },
                        "gravity_m_s2": {
                            "type": "number",
                            "default": 9.80665,
                            "description": "Gravitational acceleration, m/s^2.",
                        },
                    },
                    "required": ["links", "joints"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "loads": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "joint_name": {"type": "string"},
                                    "supported_mass_kg": {"type": "number"},
                                    "reaction_force_n": {"type": "array"},
                                    "reaction_moment_n_mm": {"type": "array"},
                                },
                            },
                        },
                        "worst_joint": {
                            "type": "object",
                            "properties": {
                                "joint_name": {"type": "string"},
                                "supported_mass_kg": {"type": "number"},
                                "reaction_force_n": {"type": "array"},
                                "reaction_moment_n_mm": {"type": "array"},
                            },
                        },
                    },
                },
                phase=1,
                resource_limits=ResourceLimits(max_memory_mb=64, max_cpu_seconds=5),
            ),
            handler=self.handle_compute_joint_loads,
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

        # FORGE-234/FORGE-281: neither analysis type has a meaningful
        # default material/boundary-condition, so these are all required
        # here (not in the JSON schema's own 'required' list, since which
        # fields are required depends on analysis_type).
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
        elif analysis_type == "modal":
            material = arguments.get("material")
            fixed_node_set = arguments.get("fixed_node_set")
            missing = [
                name
                for name, value in (("material", material), ("fixed_node_set", fixed_node_set))
                if not value
            ]
            if missing:
                raise ValueError(
                    f"calculix.run_fea: {', '.join(missing)} required for "
                    "analysis_type='modal' -- there is no default material or boundary "
                    "condition to build a real analysis deck around."
                )
            if not isinstance(material, dict):
                raise ValueError("calculix.run_fea: 'material' must be an object")
            material_name = material.get("name")
            if not material_name:
                raise ValueError(
                    "calculix.run_fea: material.name is required for analysis_type="
                    "'modal' -- density (needed for the mass matrix) is only ever "
                    "looked up by name in this codebase, even when explicit elastic "
                    "properties are also given."
                )
            youngs_modulus_mpa, poissons_ratio = resolve_elastic_properties(
                material=material_name,
                youngs_modulus_mpa=material.get("youngs_modulus_mpa"),
                poissons_ratio=material.get("poissons_ratio"),
            )
            density_kg_m3 = resolve_density_kg_m3(material_name)
            num_modes = arguments.get("num_modes", 3)
            if not isinstance(num_modes, int) or num_modes < 1:
                raise ValueError("calculix.run_fea: 'num_modes' must be a positive integer")
            deck_spec = {
                "youngs_modulus_mpa": youngs_modulus_mpa,
                "poissons_ratio": poissons_ratio,
                # FORGE-281: kg/m^3 -> tonne/mm^3, the mass unit the mm+N+MPa
                # consistent system forces -- see build_modal_deck's own
                # docstring for why this conversion can't be skipped.
                "density_tonne_mm3": density_kg_m3 * 1e-12,
                "fixed_node_set": fixed_node_set,
                "num_modes": num_modes,
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

    async def handle_cross_check_cantilever_beam(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Euler-Bernoulli hand-calc cross-check for a tip-loaded cantilever."""
        required = ("length_mm", "width_mm", "height_mm", "force_n", "fea_max_stress_mpa")
        missing = [name for name in required if arguments.get(name) is None]
        if missing:
            raise ValueError(f"Missing required field(s): {', '.join(missing)}")

        with tracer.start_as_current_span("calculix.cross_check_cantilever_beam") as span:
            try:
                result = cross_check_cantilever_bending(
                    length_mm=float(arguments["length_mm"]),
                    width_mm=float(arguments["width_mm"]),
                    height_mm=float(arguments["height_mm"]),
                    force_n=float(arguments["force_n"]),
                    fea_max_stress_mpa=float(arguments["fea_max_stress_mpa"]),
                    tolerance_pct=float(arguments.get("tolerance_pct", 20.0)),
                )
            except ValueError as exc:
                span.record_exception(exc)
                raise
            span.set_attribute("calculix.within_tolerance", result["within_tolerance"])
            return result

    async def handle_cross_check_cantilever_frequency(
        self, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """Closed-form first-mode natural-frequency cross-check for a
        tip-free cantilever (FORGE-281)."""
        required = (
            "length_mm",
            "width_mm",
            "height_mm",
            "density_kg_m3",
            "youngs_modulus_mpa",
            "fea_first_mode_hz",
        )
        missing = [name for name in required if arguments.get(name) is None]
        if missing:
            raise ValueError(f"Missing required field(s): {', '.join(missing)}")

        with tracer.start_as_current_span("calculix.cross_check_cantilever_frequency") as span:
            try:
                result = cross_check_cantilever_frequency(
                    length_mm=float(arguments["length_mm"]),
                    width_mm=float(arguments["width_mm"]),
                    height_mm=float(arguments["height_mm"]),
                    density_kg_m3=float(arguments["density_kg_m3"]),
                    youngs_modulus_mpa=float(arguments["youngs_modulus_mpa"]),
                    fea_first_mode_hz=float(arguments["fea_first_mode_hz"]),
                    tolerance_pct=float(arguments.get("tolerance_pct", 20.0)),
                )
            except ValueError as exc:
                span.record_exception(exc)
                raise
            span.set_attribute("calculix.within_tolerance", result["within_tolerance"])
            return result

    async def handle_cross_check_thermal_steady_state(
        self, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """1D steady-state conduction cross-check for a fixed-temperature
        sink (FORGE-282)."""
        required = (
            "conduction_length_mm",
            "cross_section_area_mm2",
            "thermal_conductivity_w_mk",
            "power_dissipation_w",
            "sink_temp_c",
            "fea_peak_temp_c",
        )
        missing = [name for name in required if arguments.get(name) is None]
        if missing:
            raise ValueError(f"Missing required field(s): {', '.join(missing)}")

        with tracer.start_as_current_span("calculix.cross_check_thermal_steady_state") as span:
            try:
                result = cross_check_thermal_steady_state(
                    conduction_length_mm=float(arguments["conduction_length_mm"]),
                    cross_section_area_mm2=float(arguments["cross_section_area_mm2"]),
                    thermal_conductivity_w_mk=float(arguments["thermal_conductivity_w_mk"]),
                    power_dissipation_w=float(arguments["power_dissipation_w"]),
                    sink_temp_c=float(arguments["sink_temp_c"]),
                    fea_peak_temp_c=float(arguments["fea_peak_temp_c"]),
                    tolerance_pct=float(arguments.get("tolerance_pct", 20.0)),
                )
            except ValueError as exc:
                span.record_exception(exc)
                raise
            span.set_attribute("calculix.within_tolerance", result["within_tolerance"])
            return result

    async def handle_check_mesh_convergence(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Compare max stress across already-run element sizes for convergence."""
        points = arguments.get("points")
        if not points:
            raise ValueError("points is required")

        with tracer.start_as_current_span("calculix.check_mesh_convergence") as span:
            try:
                result = check_mesh_convergence(
                    points=points,
                    tolerance_pct=float(arguments.get("tolerance_pct", 5.0)),
                )
            except (ValueError, KeyError) as exc:
                span.record_exception(exc)
                raise
            span.set_attribute("calculix.converged", result["converged"])
            return result

    async def handle_compute_joint_loads(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Quasi-static joint reaction loads for a posed serial chain (FORGE-283)."""
        links_arg = arguments.get("links")
        joints_arg = arguments.get("joints")
        if not links_arg or not joints_arg:
            raise ValueError("links and joints are both required")

        with tracer.start_as_current_span("calculix.compute_joint_loads") as span:
            try:
                links = [
                    ChainLink(
                        name=link["name"],
                        com_world_mm=tuple(link["com_world_mm"]),
                        mass_kg=float(link["mass_kg"]),
                    )
                    for link in links_arg
                ]
                joints = [
                    ChainJoint(
                        name=joint["name"],
                        position_world_mm=tuple(joint["position_world_mm"]),
                    )
                    for joint in joints_arg
                ]
                payload_position = arguments.get("payload_position_world_mm")
                loads = compute_joint_loads(
                    links=links,
                    joints=joints,
                    payload_mass_kg=float(arguments.get("payload_mass_kg", 0.0)),
                    payload_position_world_mm=(
                        tuple(payload_position) if payload_position is not None else None
                    ),
                    gravity_m_s2=float(arguments.get("gravity_m_s2", 9.80665)),
                )
                worst = worst_joint_load(loads)
            except (ValueError, KeyError) as exc:
                span.record_exception(exc)
                raise

            def _serialize(load: Any) -> dict[str, Any]:
                return {
                    "joint_name": load.joint_name,
                    "supported_mass_kg": load.supported_mass_kg,
                    "reaction_force_n": list(load.reaction_force_n),
                    "reaction_moment_n_mm": list(load.reaction_moment_n_mm),
                }

            span.set_attribute("calculix.worst_joint", worst.joint_name)
            return {
                "loads": [_serialize(load) for load in loads],
                "worst_joint": _serialize(worst),
            }

    async def run_thermal(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Execute CalculiX steady-state conduction thermal analysis (FORGE-282).

        Validates arguments and delegates to _execute_thermal_solver().
        """
        mesh_file = arguments.get("mesh_file", "")
        analysis_mode = arguments.get("analysis_mode", "steady_state")

        if not mesh_file:
            raise ValueError("mesh_file is required")
        if analysis_mode != "steady_state":
            raise ValueError(
                f"calculix.run_thermal: analysis_mode={analysis_mode!r} is not "
                "implemented -- only 'steady_state' has a real deck builder. There "
                "is no time-stepping (*HEAT TRANSFER without STEADY STATE) deck "
                "builder yet."
            )

        material = arguments.get("material")
        heat_source_node_set = arguments.get("heat_source_node_set")
        power_dissipation_w = arguments.get("power_dissipation_w")
        sink_node_set = arguments.get("sink_node_set")
        sink_temp_c = arguments.get("sink_temp_c")
        missing = [
            name
            for name, value in (
                ("material", material),
                ("heat_source_node_set", heat_source_node_set),
                ("power_dissipation_w", power_dissipation_w),
                ("sink_node_set", sink_node_set),
                ("sink_temp_c", sink_temp_c),
            )
            if value is None or value == ""
        ]
        if missing:
            raise ValueError(
                f"calculix.run_thermal: {', '.join(missing)} required -- there is no "
                "default material or boundary condition to build a real thermal "
                "deck around."
            )
        if not isinstance(material, dict):
            raise ValueError("calculix.run_thermal: 'material' must be an object")
        conductivity_w_mk = resolve_thermal_conductivity_w_mk(
            material=material.get("name"),
            thermal_conductivity_w_mk=material.get("thermal_conductivity_w_mk"),
        )
        deck_spec = {
            # FORGE-282: W/(m*K) -> W/(mm*K) -- see build_thermal_deck's own
            # docstring for why this conversion can't be skipped.
            "conductivity_w_mm_k": conductivity_w_mk * 1e-3,
            "heat_source_node_set": heat_source_node_set,
            "power_dissipation_w": float(power_dissipation_w),
            "sink_node_set": sink_node_set,
            "sink_temp_c": float(sink_temp_c),
        }

        logger.info(
            "Running thermal analysis",
            mesh_file=mesh_file,
            mode=analysis_mode,
            deck_spec=deck_spec,
        )

        result = await self._execute_thermal_solver(mesh_file, deck_spec)
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

        FORGE-234/FORGE-281: when ``deck_spec`` is given (always true for
        ``analysis_type`` 'static_stress' and 'modal' -- see ``run_fea``),
        ``mesh_file`` is treated as mesh-only topology (nodes + elements, no
        analysis cards -- exactly what freecad.generate_mesh produces) and a
        complete, solvable deck is built around it first (only the volume
        elements, real material/section/boundary/[load]/output-request
        cards -- see ``deck_builder.build_static_stress_deck``/
        ``build_modal_deck``), written to a new ``<stem>_solved.inp`` file,
        and THAT is what actually gets solved.
        """
        with tracer.start_as_current_span("calculix.execute_solver") as span:
            span.set_attribute("calculix.mesh_file", mesh_file)
            span.set_attribute("calculix.analysis_type", analysis_type)

            try:
                solved_file = mesh_file
                span_warning: str | None = None
                if deck_spec is not None:
                    mesh = parse_mesh_inp(mesh_file)
                    # FORGE-239: check BEFORE solving -- the warning is about
                    # the boundary condition choice itself, not the result,
                    # so there's no reason to wait for a (possibly slow)
                    # solve to surface it.
                    span_warning = _fixed_node_set_span_warning(mesh, deck_spec["fixed_node_set"])
                    deck_text = (
                        build_modal_deck(mesh, **deck_spec)
                        if analysis_type == "modal"
                        else build_static_stress_deck(mesh, **deck_spec)
                    )
                    solved_path = Path(mesh_file).with_name(f"{Path(mesh_file).stem}_solved.inp")
                    solved_path.write_text(deck_text, encoding="utf-8")
                    solved_file = str(solved_path)
                    span.set_attribute("calculix.solved_file", solved_file)
                if span_warning:
                    span.set_attribute("calculix.fixed_node_set_warning", span_warning)
                    logger.warning(
                        "run_fea_fixed_node_set_span_suspect",
                        mesh_file=mesh_file,
                        fixed_node_set=deck_spec["fixed_node_set"] if deck_spec else None,
                        warning=span_warning,
                    )

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
                frd_path = frd_files[0]
                parsed = parse_frd_file(frd_path)
                result: dict[str, Any] = {
                    "max_von_mises": {
                        "global": parsed.get("stress", {}).get("max", 0.0),
                    },
                    "solver_time": solver_result["solver_time_s"],
                    "mesh_elements": parsed.get("node_count", 0),
                    "result_files": solver_result["result_files"],
                    # FORGE-239: reported live -- run_fea writes
                    # "<stem>_solved.frd", but the model called
                    # extract_results on "<stem>.frd" (not found), because
                    # the exact path was only ever buried inside
                    # 'result_files' (a mixed list of every solver output
                    # file, not just the .frd). Surface it directly.
                    "frd_path": frd_path,
                    "stress": parsed.get("stress", {}),
                    "displacement": parsed.get("displacement", {}),
                }
                if analysis_type == "modal":
                    # FORGE-281: the actual eigenfrequencies live in the .dat
                    # file, not the .frd -- see parse_frequencies_dat's own
                    # docstring. A modal deck requests no *EL FILE, so the
                    # .frd's own "stress" block above is always empty here;
                    # that's expected, not a solver problem.
                    dat_files = [
                        f for f in solver_result.get("result_files", []) if f.endswith(".dat")
                    ]
                    if not dat_files:
                        raise SolverError(
                            "CalculiX exited successfully but produced no .dat result "
                            "file -- nothing was actually solved for this *FREQUENCY step."
                        )
                    result["frequencies_hz"] = parse_frequencies_dat(dat_files[0])
                if span_warning:
                    result["warnings"] = [span_warning]
                return result

            except Exception as exc:
                span.record_exception(exc)
                raise

    async def _execute_thermal_solver(
        self,
        mesh_file: str,
        deck_spec: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute CalculiX steady-state conduction thermal solver (FORGE-282).

        Same "mesh_file is topology-only, build a real deck around it first"
        shape as ``_execute_solver`` -- see that method's own docstring; the
        FORGE-232 "no .frd at all" empty-result check applies identically
        here.
        """
        with tracer.start_as_current_span("calculix.execute_thermal_solver") as span:
            span.set_attribute("calculix.mesh_file", mesh_file)

            try:
                mesh = parse_mesh_inp(mesh_file)
                # FORGE-282 follow-up: same FORGE-239 wrong-face check
                # run_fea's fixed_node_set already gets, applied to BOTH
                # thermal node sets -- checked BEFORE solving, since this is
                # about the boundary-condition choice itself, not the
                # result. See _fixed_node_set_span_warning's own docstring
                # for the real live-validation miss this closes.
                span_warnings = [
                    warning
                    for warning in (
                        _fixed_node_set_span_warning(
                            mesh,
                            deck_spec["heat_source_node_set"],
                            role="heat_source_node_set",
                        ),
                        _fixed_node_set_span_warning(
                            mesh, deck_spec["sink_node_set"], role="sink_node_set"
                        ),
                    )
                    if warning is not None
                ]
                if span_warnings:
                    span.set_attribute("calculix.node_set_warnings", span_warnings)
                    logger.warning(
                        "run_thermal_node_set_span_suspect",
                        mesh_file=mesh_file,
                        heat_source_node_set=deck_spec["heat_source_node_set"],
                        sink_node_set=deck_spec["sink_node_set"],
                        warnings=span_warnings,
                    )

                deck_text = build_thermal_deck(mesh, **deck_spec)
                solved_path = Path(mesh_file).with_name(f"{Path(mesh_file).stem}_solved.inp")
                solved_path.write_text(deck_text, encoding="utf-8")
                solved_file = str(solved_path)
                span.set_attribute("calculix.solved_file", solved_file)

                solver_result = await solver_run_fea(
                    mesh_file=solved_file,
                    load_case="thermal",
                    analysis_type="static_stress",  # ccx uses same binary
                    timeout=self.config.max_solve_time,
                    ccx_binary=self.config.ccx_binary,
                    work_dir=self.config.work_dir,
                )

                # FORGE-232: same "no result at all" empty-result check as
                # _execute_solver above.
                frd_files = [f for f in solver_result.get("result_files", []) if f.endswith(".frd")]
                if not frd_files:
                    raise SolverError(
                        "CalculiX exited successfully but produced no .frd result file -- "
                        "nothing was actually solved (check the deck has a *STEP with a "
                        "real heat source/sink and *NODE FILE output request)."
                    )
                frd_path = frd_files[0]
                parsed = parse_frd_file(frd_path)
                temperature = parsed.get("temperature", {})
                if not temperature.get("nodes"):
                    raise SolverError(
                        "CalculiX exited successfully and produced a .frd file, but it "
                        "has no NDTEMP (temperature) block -- the thermal deck's *NODE "
                        "FILE request must be missing NT."
                    )
                result: dict[str, Any] = {
                    "max_temperature_c": temperature.get("max", 0.0),
                    "min_temperature_c": temperature.get("min", 0.0),
                    "solver_time": solver_result["solver_time_s"],
                    "result_files": solver_result["result_files"],
                    "frd_path": frd_path,
                }
                if span_warnings:
                    result["warnings"] = span_warnings
                return result

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
