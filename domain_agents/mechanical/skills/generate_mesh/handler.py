"""Handler for the generate_mesh skill."""

from __future__ import annotations

from pathlib import PurePosixPath

from skill_registry.skill_base import SkillBase

from .schema import GenerateMeshInput, GenerateMeshOutput, MeshQualityMetrics

SUPPORTED_EXTENSIONS = {".step", ".stp", ".stl", ".brep"}
SUPPORTED_ALGORITHMS = {"netgen", "gmsh", "mefisto"}
SUPPORTED_OUTPUT_FORMATS = {"inp", "unv", "stl"}


class GenerateMeshHandler(SkillBase[GenerateMeshInput, GenerateMeshOutput]):
    """Generates FEA mesh from CAD geometry via FreeCAD MCP tool.

    This skill invokes the ``freecad.generate_mesh`` MCP tool to produce a
    finite element mesh from a CAD model, then evaluates the resulting mesh
    quality against user-defined thresholds.
    """

    input_type = GenerateMeshInput
    output_type = GenerateMeshOutput

    async def validate_preconditions(self, input_data: GenerateMeshInput) -> list[str]:
        """Check that the work_product exists and FreeCAD meshing tool is available."""
        errors: list[str] = []

        # Check work_product exists in the Twin
        work_product = await self.context.twin.get_work_product(
            input_data.work_product_id, branch=self.context.branch
        )
        if work_product is None:
            errors.append(f"WorkProduct {input_data.work_product_id} not found in Twin")

        # Check FreeCAD generate_mesh tool is available
        if not await self.context.mcp.is_available("freecad.generate_mesh"):
            errors.append("FreeCAD generate_mesh tool is not available")

        return errors

    async def execute(self, input_data: GenerateMeshInput) -> GenerateMeshOutput:
        """Generate mesh via FreeCAD MCP tool and assess quality."""
        self.logger.info(
            "Generating mesh",
            work_product_id=input_data.work_product_id,
            cad_file=input_data.cad_file,
            algorithm=input_data.algorithm,
            element_size=input_data.element_size,
        )

        # 1. Validate input file extension
        ext = PurePosixPath(input_data.cad_file).suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            raise ValueError(
                f"Unsupported CAD file extension '{ext}'. "
                f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
            )

        # 2. Validate algorithm
        if input_data.algorithm not in SUPPORTED_ALGORITHMS:
            raise ValueError(
                f"Unsupported meshing algorithm '{input_data.algorithm}'. "
                f"Supported: {', '.join(sorted(SUPPORTED_ALGORITHMS))}"
            )

        # 3. Validate output format
        if input_data.output_format not in SUPPORTED_OUTPUT_FORMATS:
            raise ValueError(
                f"Unsupported output format '{input_data.output_format}'. "
                f"Supported: {', '.join(sorted(SUPPORTED_OUTPUT_FORMATS))}"
            )

        # 4. Invoke freecad.generate_mesh via MCP bridge
        result = await self.context.mcp.invoke(
            "freecad.generate_mesh",
            {
                "input_file": input_data.cad_file,
                "element_size": input_data.element_size,
                "algorithm": input_data.algorithm,
                "output_format": input_data.output_format,
            },
            timeout=300,
        )

        # 5. Extract results
        mesh_file: str = result.get("mesh_file", "")
        num_nodes: int = int(result.get("num_nodes", 0))
        num_elements: int = int(result.get("num_elements", 0))
        element_types: list[str] = result.get("element_types", [])

        raw_quality: dict = result.get("quality_metrics", {})
        quality_metrics = MeshQualityMetrics(
            min_angle=float(raw_quality.get("min_angle", 0.0)),
            max_aspect_ratio=float(raw_quality.get("max_aspect_ratio", 0.0)),
            avg_quality=float(raw_quality.get("avg_quality", 0.0)),
            jacobian_ratio=float(raw_quality.get("jacobian_ratio", 0.0)),
        )

        # 6. Assess quality against thresholds
        quality_issues: list[str] = []

        if quality_metrics.min_angle > 0 and (
            quality_metrics.min_angle < input_data.min_angle_threshold
        ):
            quality_issues.append(
                f"Minimum element angle {quality_metrics.min_angle:.1f} deg "
                f"is below threshold {input_data.min_angle_threshold:.1f} deg"
            )

        if quality_metrics.max_aspect_ratio > 0 and (
            quality_metrics.max_aspect_ratio > input_data.max_aspect_ratio_threshold
        ):
            quality_issues.append(
                f"Maximum aspect ratio {quality_metrics.max_aspect_ratio:.1f} "
                f"exceeds threshold {input_data.max_aspect_ratio_threshold:.1f}"
            )

        quality_acceptable = len(quality_issues) == 0

        return GenerateMeshOutput(
            work_product_id=input_data.work_product_id,
            mesh_file=mesh_file,
            num_nodes=num_nodes,
            num_elements=num_elements,
            element_types=element_types,
            quality_metrics=quality_metrics,
            quality_acceptable=quality_acceptable,
            quality_issues=quality_issues,
            algorithm_used=input_data.algorithm,
            element_size_used=input_data.element_size,
        )

    async def validate_output(self, output: GenerateMeshOutput) -> list[str]:
        """Verify that the mesh has nodes and elements."""
        errors: list[str] = []
        if output.num_nodes <= 0:
            errors.append("Mesh has zero nodes")
        if output.num_elements <= 0:
            errors.append("Mesh has zero elements")
        return errors
