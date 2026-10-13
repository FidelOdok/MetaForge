"""MCP adapter for vendor connectors (FORGE-427).

``vendor.list_connectors`` and ``vendor.order_status`` read. ``vendor.quote``
records the quote in the twin (additive, held like any write).
``vendor.place_order`` can spend money, so it is destructive and held for
the person on every call; nothing here approves it.

The adapter takes one service object, in-process in the gateway or over HTTP
from the sidecar (``metaforge.mcp.remote_vendors``).
"""

from __future__ import annotations

from typing import Any, Protocol

import structlog

from observability.tracing import get_tracer
from tool_registry.mcp_server.handlers import ResourceLimits, ToolManifest
from tool_registry.mcp_server.server import McpToolServer

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.tools.vendor.adapter")

SERVICES = [
    "pcb_fabrication",
    "pcb_assembly",
    "stencil",
    "additive",
    "cnc",
    "sheet_metal",
    "laser_cutting",
    "moulding",
    "components",
]


class VendorService(Protocol):
    async def list_connectors(self) -> dict[str, Any]: ...

    async def quote(self, request: dict[str, Any], by: str) -> dict[str, Any]: ...

    async def place_order(self, quote_id: str, by: str) -> dict[str, Any]: ...

    async def order_status(self, order_id: str) -> dict[str, Any]: ...


def _limits() -> ResourceLimits:
    return ResourceLimits(max_memory_mb=256, max_cpu_seconds=120, max_disk_mb=1)


class VendorServer(McpToolServer):
    """Serves ``vendor.*``."""

    def __init__(self, vendors: VendorService) -> None:
        super().__init__(adapter_id="vendor", version="0.1.0")
        self._vendors = vendors
        self.register_tool(
            manifest=ToolManifest(
                tool_id="vendor.list_connectors",
                adapter_id="vendor",
                name="List Vendor Connectors",
                description=(
                    "The vendor connectors this gateway has (PCB houses, print and CNC "
                    "services, distributors), what each can quote, its mode (sandbox: "
                    "nothing is bought; production: real orders) and whether its "
                    "credential works now. An empty list means none is configured."
                ),
                capability="vendor",
                input_schema={"type": "object", "properties": {}},
                output_schema={"type": "object"},
                phase=1,
                resource_limits=_limits(),
            ),
            handler=self.list_connectors,
        )
        self.register_tool(
            manifest=ToolManifest(
                tool_id="vendor.quote",
                adapter_id="vendor",
                name="Request Vendor Quote",
                description=(
                    "Ask one connector to price a job: service, items (work_product_id for "
                    "files such as Gerbers or STEP, or part_number for parts, each with a "
                    "quantity), the order quantity and any vendor options. The quote is "
                    "recorded in the twin as a procurement record with the vendor, mode "
                    "and date, so the price is on file. Its prices are the vendor's; quote "
                    "them as given, with the mode, and never as a final cost if the mode "
                    "is sandbox."
                ),
                capability="vendor",
                input_schema={
                    "type": "object",
                    "properties": {
                        "connector_id": {"type": "string"},
                        "service": {"type": "string", "enum": SERVICES},
                        "items": {"type": "array", "items": {"type": "object"}},
                        "quantity": {"type": "integer", "minimum": 1},
                        "options": {"type": "object"},
                        "ship_to_country": {"type": "string"},
                        "project_id": {"type": "string"},
                    },
                    "required": ["connector_id", "service", "items"],
                },
                output_schema={"type": "object"},
                phase=1,
                resource_limits=_limits(),
            ),
            handler=self.quote,
        )
        self.register_tool(
            manifest=ToolManifest(
                tool_id="vendor.place_order",
                adapter_id="vendor",
                name="Place Vendor Order",
                description=(
                    "Order a stored quote. In production mode this spends money. Held for "
                    "the person on every call; never retry a refused or timed-out order "
                    "without asking. Refused when the quote has expired, was already "
                    "ordered, or the connector's mode changed since the quote. The order "
                    "is recorded in the twin, linked to its quote."
                ),
                capability="vendor",
                input_schema={
                    "type": "object",
                    "properties": {"quote_id": {"type": "string"}},
                    "required": ["quote_id"],
                },
                output_schema={"type": "object"},
                phase=1,
                resource_limits=_limits(),
            ),
            handler=self.place_order,
        )
        self.register_tool(
            manifest=ToolManifest(
                tool_id="vendor.order_status",
                adapter_id="vendor",
                name="Vendor Order Status",
                description=(
                    "Where an order is, as the vendor reports it: placed, in_review, "
                    "in_production, shipped (with tracking when given), delivered, "
                    "cancelled or failed. Reads only."
                ),
                capability="vendor",
                input_schema={
                    "type": "object",
                    "properties": {"order_id": {"type": "string"}},
                    "required": ["order_id"],
                },
                output_schema={"type": "object"},
                phase=1,
                resource_limits=_limits(),
            ),
            handler=self.order_status,
        )

    async def list_connectors(self, arguments: dict[str, Any]) -> dict[str, Any]:
        with tracer.start_as_current_span("vendor.list_connectors"):
            return await self._vendors.list_connectors()

    async def quote(self, arguments: dict[str, Any]) -> dict[str, Any]:
        with tracer.start_as_current_span("vendor.quote"):
            result = await self._vendors.quote(dict(arguments), "mcp")
            logger.info("vendor_quote_tool", connector=arguments.get("connector_id"))
            return result

    async def place_order(self, arguments: dict[str, Any]) -> dict[str, Any]:
        with tracer.start_as_current_span("vendor.place_order"):
            return await self._vendors.place_order(str(arguments["quote_id"]), "mcp")

    async def order_status(self, arguments: dict[str, Any]) -> dict[str, Any]:
        with tracer.start_as_current_span("vendor.order_status"):
            return await self._vendors.order_status(str(arguments["order_id"]))
