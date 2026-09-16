"""BOMItem node — a line item in a Bill of Materials with AAS-aligned properties."""

from uuid import UUID, uuid4

from pydantic import Field

from twin_core.models.base import NodeBase
from twin_core.models.enums import NodeType


class BOMItem(NodeBase):
    """A single line item in a Bill of Materials.

    Extends the Component concept with procurement and AAS (Asset Administration
    Shell) compatibility fields. The ``global_asset_id`` follows the URN convention
    ``urn:metaforge:bom:<manufacturer>:<mpn>``.
    """

    id: UUID = Field(default_factory=uuid4)
    node_type: NodeType = NodeType.BOM_ITEM
    part_number: str
    manufacturer: str
    description: str = ""
    quantity: int = 1
    reference_designators: list[str] = Field(default_factory=list)
    unit_cost: float | None = None
    specifications: dict = Field(default_factory=dict)
    global_asset_id: str | None = None
    supplier: str | None = None
    datasheet_url: str | None = None
    image_url: str | None = None
    footprint: str | None = None
    """PCB land-pattern/footprint identifier (e.g. an IPC-7351 name) —
    distinct from a coarser package family name, which callers are free to
    put in ``specifications`` instead."""
    cad_model_url: str | None = None
