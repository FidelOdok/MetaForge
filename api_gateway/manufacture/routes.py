"""Manufacture-release API (FORGE-294, gap G-H2).

``GET /v1/manufacture/release`` wires the dashboard's "Release for
manufacture" wizard to the real
``api_gateway.twin.manufacture_release.make_manufacture_release``
evaluator -- the same "thin REST wrapper over an injected evaluator
closure" pattern ``api_gateway/dfm/routes.py`` established for FORGE-273.

A GET (not POST) deliberately: the dashboard's existing work-product
download link (``GET /v1/twin/nodes/{id}/file``) is a plain ``<a href>``
navigation, no JS blob plumbing needed. This route can't be a raw binary
``Response`` the same way, though -- the sample/demo-mode dashboard answers
every request through an axios adapter that only ever returns parsed JSON
(see ``dashboard/src/lib/sample-workspace.ts``), so a raw-bytes route
would be unreachable in demo mode and untestable in Playwright. The
response is therefore JSON with a base64 body, mirroring
``cadquery.export_geometry``'s own ``step_base64`` precedent; the
dashboard decodes it into a ``Blob`` client-side and triggers the browser
download from there (same pattern ``BomPage.tsx``'s CSV export already
uses for a client-built blob).
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.manufacture")

router = APIRouter(prefix="/v1/manufacture", tags=["manufacture"])

_release: Any = None


def init_manufacture_release(releaser: Any) -> None:
    """Bind the real ``release(...)`` closure (built in
    ``api_gateway/server.py`` from ``make_manufacture_release``)."""
    global _release  # noqa: PLW0603
    _release = releaser
    logger.info("manufacture_release_initialized")


class ManufactureReleaseResponse(BaseModel):
    work_product_id: str
    process: str
    format: str
    filename: str
    file_size_bytes: int
    content_base64: str


@router.get("/release", response_model=ManufactureReleaseResponse)
async def release_for_manufacture(
    work_product_id: str = Query(...),
    process: str = Query(..., description="'3d_print' (-> STL) or 'cnc' (-> STEP)"),
) -> ManufactureReleaseResponse:
    if _release is None:
        raise HTTPException(status_code=503, detail="manufacture release not configured")
    with tracer.start_as_current_span("manufacture.release_route") as span:
        span.set_attribute("manufacture.work_product_id", work_product_id)
        span.set_attribute("manufacture.process", process)
        try:
            result = await _release(work_product_id=work_product_id, process=process)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 -- surface a clean 502 with the cause
            logger.warning("manufacture_release_failed", error=str(exc))
            span.record_exception(exc)
            raise HTTPException(
                status_code=502, detail=f"manufacture release failed: {exc}"
            ) from exc
        return ManufactureReleaseResponse(**result)
