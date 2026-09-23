"""Input/output schemas for the create_assembly skill."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class AssemblyPart(BaseModel):
    """Definition of a part in the assembly.

    Reference the part either by ``node_id`` (the Twin node of an
    already-committed part -- resolved internally via
    ``twin.stage_work_product_file``, the durable path every other
    committed-geometry reference in this system uses) or by a raw ``file``
    path (for a STEP file already sitting on the shared adapter workspace,
    e.g. one generated earlier in the same turn and not yet committed).
    Exactly one must be given -- a raw adapter-ephemeral path silently goes
    stale the moment a later call reuses the same filename (FORGE-85).
    """

    name: str = Field(..., min_length=1, description="Part name (unique within assembly)")
    node_id: str | None = Field(
        default=None,
        description="Twin node ID of an already-committed part to include in the assembly",
    )
    file: str = Field(
        default="",
        description="Path to a STEP file already on the shared adapter workspace",
    )
    location: dict[str, float] = Field(
        default_factory=dict,
        description="Position and rotation: x, y, z, rx, ry, rz (defaults to origin)",
    )

    @model_validator(mode="after")
    def _exactly_one_reference(self) -> AssemblyPart:
        if bool(self.node_id) == bool(self.file):
            raise ValueError(
                f"part {self.name!r}: give exactly one of node_id or file, not both/neither"
            )
        return self


class AssemblyConstraint(BaseModel):
    """Constraint between two parts in the assembly."""

    part_a: str = Field(..., description="First part name")
    part_b: str = Field(..., description="Second part name")
    type: str = Field(
        ...,
        description="Constraint type: Point, Axis, Plane, PointInPlane",
    )


class CreateAssemblyInput(BaseModel):
    """Input for the create_assembly skill."""

    work_product_id: UUID | None = Field(
        default=None,
        description=(
            "Twin work_product ID for the assembly, when linking to an existing "
            "one. Optional -- commit_geometry creates a fresh CAD_MODEL work "
            "product when omitted, same as generate_cad."
        ),
    )
    parts: list[AssemblyPart] = Field(
        ...,
        min_length=1,
        description="List of parts to assemble",
    )
    constraints: list[AssemblyConstraint] = Field(
        default_factory=list,
        description="Assembly constraints between parts",
    )
    output_path: str = Field(
        default="",
        description="Optional output STEP file path",
    )
    material: str = Field(default="ABS", description="Material name for metadata")
    project_id: str | None = Field(
        default=None,
        description="Project UUID to link the resulting work product to, when committed",
    )
    commit: bool = Field(
        default=True,
        description=(
            "Persist the generated assembly into the Twin via twin.commit_geometry "
            "immediately (best-effort -- failure is reported on the output, not raised)"
        ),
    )


class CreateAssemblyOutput(BaseModel):
    """Output from the create_assembly skill."""

    work_product_id: UUID | None = Field(default=None, description="Twin work_product ID")
    assembly_file: str = Field(..., description="Path to generated assembly STEP file")
    part_count: int = Field(..., ge=0, description="Number of parts in the assembly")
    total_volume: float = Field(..., ge=0, description="Total volume of all parts in mm^3")
    interference_check_passed: bool = Field(
        ..., description="Whether assembly passed interference check"
    )
    committed: bool = Field(
        default=False,
        description="Whether the assembly was persisted into the Twin as a cad_model work product",
    )
    twin_node_id: str | None = Field(
        default=None, description="Twin node ID of the committed cad_model, when committed"
    )
    model_url: str | None = Field(
        default=None, description="Viewer URL of the committed cad_model, when committed"
    )
    commit_error: str | None = Field(
        default=None,
        description="Set when commit=True was requested but persistence was skipped or failed",
    )
