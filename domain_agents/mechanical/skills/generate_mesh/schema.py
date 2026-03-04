"""Input/output schemas for the generate_mesh skill."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class MeshQualityMetrics(BaseModel):
    """Quality metrics for the generated mesh."""

    min_angle: float = Field(default=0.0, description="Minimum element angle in degrees")
    max_aspect_ratio: float = Field(default=0.0, description="Maximum element aspect ratio")
    avg_quality: float = Field(default=0.0, description="Average element quality (0-1)")
    jacobian_ratio: float = Field(default=0.0, description="Jacobian quality ratio")


class GenerateMeshInput(BaseModel):
    """Input for the generate_mesh skill."""

    artifact_id: str = Field(..., description="Twin artifact ID for the CAD model")
    cad_file: str = Field(..., min_length=1, description="Path to input CAD file (STEP/STL/BREP)")
    element_size: float = Field(
        default=1.0, gt=0, description="Target element size in mm"
    )
    algorithm: str = Field(default="netgen", description="Meshing algorithm: netgen, gmsh, mefisto")
    output_format: str = Field(default="inp", description="Output format: inp, unv, stl")
    min_angle_threshold: float = Field(
        default=15.0, ge=0, description="Minimum acceptable angle in degrees"
    )
    max_aspect_ratio_threshold: float = Field(
        default=10.0, gt=0, description="Maximum acceptable aspect ratio"
    )
    refinement_regions: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Optional refinement regions: [{'name': '...', 'element_size': ...}]",
    )


class GenerateMeshOutput(BaseModel):
    """Output from the generate_mesh skill."""

    artifact_id: str = Field(..., description="Twin artifact ID")
    mesh_file: str = Field(..., description="Path to generated mesh file")
    num_nodes: int = Field(..., ge=0, description="Number of mesh nodes")
    num_elements: int = Field(..., ge=0, description="Number of mesh elements")
    element_types: list[str] = Field(
        default_factory=list, description="Element types used (e.g., ['C3D10', 'C3D4'])"
    )
    quality_metrics: MeshQualityMetrics = Field(
        default_factory=MeshQualityMetrics, description="Mesh quality metrics"
    )
    quality_acceptable: bool = Field(
        ..., description="Whether mesh meets quality thresholds"
    )
    quality_issues: list[str] = Field(
        default_factory=list, description="Human-readable quality issues"
    )
    algorithm_used: str = Field(..., description="Meshing algorithm that was used")
    element_size_used: float = Field(..., description="Element size that was used")
