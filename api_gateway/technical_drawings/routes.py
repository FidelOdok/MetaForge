"""Technical drawing listing API (FORGE-293, gap G-H1).

``GET /v1/technical-drawings`` wires the dashboard's "Drawing viewer per
part" to ``api_gateway.twin.technical_drawing_viewer.make_technical_drawing_lister``
-- the same thin-REST-wrapper-over-an-injected-closure pattern
``api_gateway/bringup/routes.py`` established for ``twin.create_bringup_checklist``.
Approval (``POST .../approve-technical-drawing``) lives in
``api_gateway/twin/routes.py`` alongside ``approve-sketch``, since it's a
node-scoped action under ``/v1/twin/nodes/{id}``, not a listing.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, HTTPException

from api_gateway.twin.schemas import TechnicalDrawingListResponse, TechnicalDrawingSummary
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.technical_drawings")

router = APIRouter(prefix="/v1/technical-drawings", tags=["technical-drawings"])

_list_technical_drawings: Any = None


def init_technical_drawing_lister(lister: Any) -> None:
    """Bind the real ``list_drawings(...)`` closure (built in
    ``api_gateway/server.py`` from ``make_technical_drawing_lister``)."""
    global _list_technical_drawings  # noqa: PLW0603
    _list_technical_drawings = lister
    logger.info("technical_drawing_routes_initialized")


@router.get("", response_model=TechnicalDrawingListResponse)
async def list_technical_drawings(work_product_id: str) -> TechnicalDrawingListResponse:
    if _list_technical_drawings is None:
        raise HTTPException(status_code=503, detail="technical drawing lister not configured")
    with tracer.start_as_current_span("technical_drawings.list") as span:
        span.set_attribute("technical_drawings.work_product_id", work_product_id)
        entries = await _list_technical_drawings(work_product_id=work_product_id)
        return TechnicalDrawingListResponse(
            drawings=[TechnicalDrawingSummary(**e) for e in entries]
        )
