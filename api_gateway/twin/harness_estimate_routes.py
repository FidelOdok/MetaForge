"""Cable harness length estimate API (FORGE-275, gap G-E2).

``GET /v1/wiring/harness-estimate`` wires the dashboard's power/wiring
panel to the real ``twin.get_harness_estimate`` closure
(``api_gateway.twin.harness_estimate``) -- same thin-REST-wrapper-over-an-
injected-closure pattern ``api_gateway/firmware/routes.py`` established for
``twin.create_firmware_scaffold``.

Named ``/v1/wiring/...``, not ``/v1/harness/...`` -- ``api_gateway/harness/``
already exists for this codebase's unrelated chat/execution harness admin
routes; this is an electrical wiring harness, a different domain entirely.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.wiring")

router = APIRouter(prefix="/v1/wiring", tags=["wiring"])

_get_harness_estimate: Any = None


def init_harness_estimate(getter: Any) -> None:
    """Bind the real ``get(...)`` closure (built in ``api_gateway/server.py``
    from ``make_harness_estimate_getter``)."""
    global _get_harness_estimate  # noqa: PLW0603
    _get_harness_estimate = getter
    logger.info("harness_estimate_routes_initialized")


class HarnessJointEntry(BaseModel):
    step_number: int
    joint_name: str
    joint_type: str
    base: str
    follower: str
    segment_length_mm: float
    cable_length_estimate_mm: float


class HarnessEstimateResponse(BaseModel):
    work_product_id: str
    joints: list[HarnessJointEntry]


@router.get("/harness-estimate", response_model=HarnessEstimateResponse)
async def get_harness_estimate(
    work_product_id: str = Query(...),
) -> HarnessEstimateResponse:
    if _get_harness_estimate is None:
        raise HTTPException(status_code=503, detail="harness estimate getter not configured")
    with tracer.start_as_current_span("wiring.get_harness_estimate") as span:
        span.set_attribute("wiring.work_product_id", work_product_id)
        try:
            result = await _get_harness_estimate(work_product_id=work_product_id)
        except ValueError as exc:
            span.record_exception(exc)
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 -- surface a clean 502 with the cause
            logger.warning("harness_estimate_get_failed", error=str(exc))
            span.record_exception(exc)
            raise HTTPException(status_code=502, detail=f"harness estimate failed: {exc}") from exc
        return HarnessEstimateResponse(
            work_product_id=result["work_product_id"],
            joints=[HarnessJointEntry(**j) for j in result["joints"]],
        )
