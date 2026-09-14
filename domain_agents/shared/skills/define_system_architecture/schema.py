"""Input/output schemas for the define_system_architecture skill."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ArchComponent(BaseModel):
    """A component in the system architecture."""

    name: str = Field(..., min_length=1, description="Unique component name")
    discipline: str = Field(default="", description="e.g. mechanical, electronics, firmware")
    description: str = Field(default="", description="What this component does")


class ArchInterface(BaseModel):
    """An interface (data/signal/mechanical) between two components."""

    from_component: str = Field(
        ..., min_length=1, alias="from", description="Source component name"
    )
    to_component: str = Field(..., min_length=1, alias="to", description="Target component name")
    interface_type: str = Field(default="", description="e.g. SPI, I2C, CAN, mechanical")
    description: str = Field(default="")

    model_config = {"populate_by_name": True}


class DefineSystemArchitectureInput(BaseModel):
    """Input for the system-architecture skill."""

    project_id: str = Field(..., min_length=1, description="Project identifier")
    system_name: str = Field(..., min_length=1, description="System being architected")
    components: list[ArchComponent] = Field(..., min_length=1)
    interfaces: list[ArchInterface] = Field(default_factory=list)


class DefineSystemArchitectureOutput(BaseModel):
    """Output from the system-architecture skill."""

    node_id: str = Field(..., description="SYSTEM_ARCHITECTURE work-product node id")
    component_count: int = Field(..., ge=0)
    interface_count: int = Field(..., ge=0)
    dangling_interface_count: int = Field(
        ..., ge=0, description="Interfaces referencing an undeclared component"
    )
