"""Firmware scaffold API (FORGE-276, gap G-E3).

``POST /v1/firmware/scaffold`` wires the dashboard's Firmware panel to the
real ``twin.create_firmware_scaffold`` closure
(``api_gateway.twin.firmware_scaffold``) -- same thin-REST-wrapper-over-an-
injected-closure pattern ``api_gateway/bringup/routes.py`` established for
``twin.create_bringup_checklist``.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.firmware")

router = APIRouter(prefix="/v1/firmware", tags=["firmware"])

_create_firmware_scaffold: Any = None


def init_firmware_scaffold(creator: Any) -> None:
    """Bind the real ``create(...)`` closure (built in
    ``api_gateway/server.py`` from ``make_firmware_scaffold_creator``)."""
    global _create_firmware_scaffold  # noqa: PLW0603
    _create_firmware_scaffold = creator
    logger.info("firmware_scaffold_routes_initialized")


class CreateFirmwareScaffoldRequest(BaseModel):
    work_product_id: str
    project_id: str | None = None


class FirmwareJointEntry(BaseModel):
    joint_name: str
    joint_type: str
    can_id: int
    limits: dict[str, float] | None = None


class CreateFirmwareScaffoldResponse(BaseModel):
    pinmap_node_id: str
    firmware_source_node_id: str
    joints: list[FirmwareJointEntry]


@router.post("/scaffold", response_model=CreateFirmwareScaffoldResponse)
async def create_firmware_scaffold(
    body: CreateFirmwareScaffoldRequest,
) -> CreateFirmwareScaffoldResponse:
    if _create_firmware_scaffold is None:
        raise HTTPException(status_code=503, detail="firmware scaffold creator not configured")
    with tracer.start_as_current_span("firmware.create_scaffold") as span:
        span.set_attribute("firmware.work_product_id", body.work_product_id)
        try:
            result = await _create_firmware_scaffold(
                work_product_id=body.work_product_id, project_id=body.project_id
            )
        except ValueError as exc:
            span.record_exception(exc)
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 -- surface a clean 502 with the cause
            logger.warning("firmware_scaffold_create_failed", error=str(exc))
            span.record_exception(exc)
            raise HTTPException(
                status_code=502, detail=f"firmware scaffold creation failed: {exc}"
            ) from exc
        return CreateFirmwareScaffoldResponse(
            pinmap_node_id=result["pinmap_node_id"],
            firmware_source_node_id=result["firmware_source_node_id"],
            joints=[FirmwareJointEntry(**j) for j in result["joints"]],
        )
