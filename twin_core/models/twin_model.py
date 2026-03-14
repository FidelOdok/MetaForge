"""TwinModel node — a versioned product-level digital twin definition."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import Field

from twin_core.models.base import NodeBase
from twin_core.models.enums import NodeType


class TwinModel(NodeBase):
    """A product-level digital twin definition that aggregates work_products.

    Represents a complete product design at a specific version. The
    ``global_asset_id`` follows the URN convention
    ``urn:metaforge:model:<productId>:<version>``.
    """

    id: UUID = Field(default_factory=uuid4)
    node_type: NodeType = NodeType.TWIN_MODEL
    product_id: str
    version: str
    name: str
    description: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict = Field(default_factory=dict)
    global_asset_id: str | None = None
