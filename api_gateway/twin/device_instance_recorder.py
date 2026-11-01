"""DeviceInstance registration orchestration for twin.register_device_instance
(FORGE-321, target lifecycle spec App. B REALISE, step 11).

``DeviceInstance`` (``twin_core/models/device_instance.py``) has existed
since before this epic but was never instantiated -- no CRUD on
``TwinAPI``, no MCP tool. This module is that missing piece: a plain
registration, plus an optional ``INSTANCE_OF`` edge to the design revision
(a WorkProduct -- CAD assembly, robot_description, ...) this unit was
actually built from, when the caller has one to name. ``product_id``
(``DeviceInstance``'s own field) stays a free-text identifier exactly as
documented -- it is NOT assumed to be a resolvable graph ref, since a
device can legitimately be registered before or without a born-digital
design record on hand. ``design_revision_ref`` is the separate, optional,
resolved link for when one exists (same "resolve before construct, never
guess" discipline every recorder in this package already follows).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import structlog

from api_gateway.twin._ref_resolver import resolve_ref
from observability.tracing import get_tracer
from twin_core.models.device_instance import DeviceInstance
from twin_core.models.enums import EdgeType

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.device_instance_recorder")


def make_device_instance_registrar(twin: Any) -> Any:
    """Return an async ``register(...)`` bound to a twin."""

    async def register(
        *,
        serial_number: str,
        product_id: str,
        firmware_version: str = "",
        hardware_revision: str = "",
        manufactured_at: str | None = None,
        provisioned_at: str | None = None,
        design_revision_ref: str | None = None,
        project_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not serial_number:
            raise ValueError("twin.register_device_instance: 'serial_number' is required")
        if not product_id:
            raise ValueError("twin.register_device_instance: 'product_id' is required")

        with tracer.start_as_current_span("twin.register_device_instance") as span:
            span.set_attribute("device_instance.serial_number", serial_number)
            span.set_attribute("device_instance.product_id", product_id)

            design_revision_id = None
            if design_revision_ref:
                design_revision_id = await resolve_ref(
                    twin, design_revision_ref, project_id=project_id, include_work_products=True
                )

            instance = DeviceInstance(
                serial_number=serial_number,
                product_id=product_id,
                firmware_version=firmware_version,
                hardware_revision=hardware_revision,
                manufactured_at=datetime.fromisoformat(manufactured_at)
                if manufactured_at
                else None,
                provisioned_at=datetime.fromisoformat(provisioned_at) if provisioned_at else None,
                metadata=metadata or {},
                global_asset_id=f"urn:metaforge:device:{serial_number}",
                project_id=project_id,  # pydantic coerces str -> UUID
            )
            created = await twin.create_device_instance(instance)

            if design_revision_id is not None:
                await twin.add_edge(
                    created.id,
                    design_revision_id,
                    EdgeType.INSTANCE_OF,
                    metadata={"kind": "device_instance_of_design_revision"},
                )

            logger.info(
                "device_instance_registered",
                node_id=str(created.id),
                serial_number=serial_number,
                product_id=product_id,
                design_revision_id=str(design_revision_id) if design_revision_id else None,
            )
            return {
                "node_id": str(created.id),
                "serial_number": created.serial_number,
                "product_id": created.product_id,
                "global_asset_id": created.global_asset_id,
                "design_revision_id": str(design_revision_id) if design_revision_id else None,
            }

    return register
