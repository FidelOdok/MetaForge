"""CalculiX FEA tool adapter for MetaForge."""

from tool_registry.tools.calculix.adapter import CalculixServer
from tool_registry.tools.calculix.deck import (
    Constraint,
    Convection,
    DeckBuilder,
    DeckError,
    HeatFlux,
    LoadCase,
    PointLoad,
    Pressure,
    Region,
    StepOptions,
    ThermalBoundary,
    parse_load_cases,
)
from tool_registry.tools.calculix.materials import (
    MATERIALS,
    Material,
    UnknownMaterialError,
    get_material,
    list_materials,
    resolve_material,
)
from tool_registry.tools.calculix.mesh import (
    Element,
    Mesh,
    MeshParseError,
    parse_inp_mesh,
    select_nodes_on_face,
)
from tool_registry.tools.calculix.mesh_quality import (
    ElementQuality,
    MeshQualityReport,
    evaluate_element,
    evaluate_mesh,
)
from tool_registry.tools.calculix.result_parser import (
    FrdParseError,
    extract_results,
    parse_dat_frequencies,
    parse_frd_file,
)
from tool_registry.tools.calculix.solver import (
    SolverError,
    SolverTimeoutError,
    run_fea,
)

__all__ = [
    "MATERIALS",
    "CalculixServer",
    "Constraint",
    "Convection",
    "DeckBuilder",
    "DeckError",
    "Element",
    "ElementQuality",
    "FrdParseError",
    "HeatFlux",
    "LoadCase",
    "Material",
    "Mesh",
    "MeshParseError",
    "MeshQualityReport",
    "PointLoad",
    "Pressure",
    "Region",
    "SolverError",
    "SolverTimeoutError",
    "StepOptions",
    "ThermalBoundary",
    "UnknownMaterialError",
    "evaluate_element",
    "evaluate_mesh",
    "extract_results",
    "get_material",
    "list_materials",
    "parse_dat_frequencies",
    "parse_frd_file",
    "parse_inp_mesh",
    "parse_load_cases",
    "resolve_material",
    "run_fea",
    "select_nodes_on_face",
]
