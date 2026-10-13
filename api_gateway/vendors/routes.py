"""``/v1/vendors``: connectors, quotes and orders (FORGE-427).

The connectors and their credentials live in the gateway; the MCP sidecar
reaches them through these routes (``metaforge.mcp.remote_vendors``).
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from api_gateway.vendors import service
from api_gateway.vendors.connectors import VendorError

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/v1/vendors", tags=["vendors"])

AGENT_HEADER = "X-MetaForge-Agent"


def _by(request: Request, named: str) -> str:
    return (named or request.headers.get(AGENT_HEADER) or "").strip()[:100] or "unknown"


def _vendor_error(exc: VendorError) -> HTTPException:
    status = {"auth": 401, "rate_limited": 429, "unreachable": 502, "protocol": 502}.get(
        exc.kind, 422
    )
    return HTTPException(status_code=status, detail=f"{exc.kind}: {exc}")


class QuoteRequest(BaseModel):
    connector_id: str
    service: str
    items: list[dict[str, Any]] = Field(default_factory=list)
    quantity: int = Field(default=1, ge=1)
    options: dict[str, Any] = Field(default_factory=dict)
    ship_to_country: str | None = None
    project_id: str | None = None
    by: str = ""


class OrderRequest(BaseModel):
    quote_id: str
    by: str = ""


@router.get("/connectors")
async def list_connectors() -> dict[str, Any]:
    """Every connector with its services, mode and live auth state."""
    return {"connectors": await service.connector_report()}


@router.post("/quotes", status_code=201)
async def create_quote(body: QuoteRequest, request: Request) -> dict[str, Any]:
    data = body.model_dump(exclude={"by"})
    try:
        return await service.quote(data, by=_by(request, body.by))
    except VendorError as exc:
        raise _vendor_error(exc) from exc
    except service.VendorServiceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/orders", status_code=201)
async def create_order(body: OrderRequest, request: Request) -> dict[str, Any]:
    try:
        return await service.place_order(body.quote_id, by=_by(request, body.by))
    except VendorError as exc:
        raise _vendor_error(exc) from exc
    except service.VendorServiceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/orders/{order_id}/status")
async def get_order_status(order_id: str) -> dict[str, Any]:
    try:
        return await service.order_status(order_id)
    except VendorError as exc:
        raise _vendor_error(exc) from exc
    except service.VendorServiceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


class HandoffRequest(BaseModel):
    service: str
    work_product_ids: list[str] = Field(default_factory=list)


@router.post("/handoffs")
async def list_handoffs(body: HandoffRequest) -> dict[str, Any]:
    """Vendors with no API that take this service, and which files each accepts. Reads only."""
    from api_gateway.vendors.handoffs import handoffs_for

    return await handoffs_for(body.service, body.work_product_ids)
