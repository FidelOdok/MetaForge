"""Component node — a physical part used in the design."""

from uuid import UUID, uuid4

from pydantic import Field

from twin_core.models.base import NodeBase
from twin_core.models.enums import ComponentLifecycle, NodeType


class Component(NodeBase):
    """A physical component (IC, resistor, connector, etc.) with supply chain metadata."""

    id: UUID = Field(default_factory=uuid4)
    node_type: NodeType = NodeType.COMPONENT
    part_number: str
    manufacturer: str
    description: str = ""
    package: str = ""
    lifecycle: ComponentLifecycle = ComponentLifecycle.ACTIVE
    datasheet_url: str = ""
    specs: dict = Field(default_factory=dict)
    alternates: list[str] = Field(default_factory=list)
    unit_cost: float | None = None
    lead_time_days: int | None = None
    quantity: int = 1
