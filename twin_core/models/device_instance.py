"""DeviceInstance node — a specific manufactured unit of a product."""

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import Field

from twin_core.models.base import NodeBase
from twin_core.models.enums import NodeType


class DeviceInstance(NodeBase):
    """A specific manufactured unit (serial-number-level) of a product.

    Used for field telemetry, after-sales tracking, and digital-twin-per-device
    scenarios. The ``global_asset_id`` follows the URN convention
    ``urn:metaforge:device:<serialNumber>``.
    """

    id: UUID = Field(default_factory=uuid4)
    node_type: NodeType = NodeType.DEVICE_INSTANCE
    serial_number: str
    product_id: str
    firmware_version: str = ""
    hardware_revision: str = ""
    manufactured_at: datetime | None = None
    provisioned_at: datetime | None = None
    metadata: dict = Field(default_factory=dict)
    global_asset_id: str | None = None
