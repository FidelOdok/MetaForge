"""Repeatability estimate API (FORGE-285, gap G-F9 -- the buildable half).

``GET /v1/controls/repeatability-estimate`` wires the dashboard/CLI to the
real ``twin.get_repeatability_estimate`` closure
(``api_gateway.twin.repeatability``) -- same thin-REST-wrapper-over-an-
injected-closure pattern ``api_gateway/twin/harness_estimate_routes.py``
established for ``twin.get_harness_estimate``.

Named ``/v1/controls/...`` since the ticket's own capability name is
"Controls validation"; only the repeatability half is wired here -- see
``api_gateway.twin.repeatability``'s module docstring for why the
tracking-error/stability-margin half is deliberately not built.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.controls")

router = APIRouter(prefix="/v1/controls", tags=["controls"])

_get_repeatability_estimate: Any = None


def init_repeatability_estimate(getter: Any) -> None:
    """Bind the real ``get(...)`` closure (built in ``api_gateway/server.py``
    from ``make_repeatability_estimator``)."""
    global _get_repeatability_estimate  # noqa: PLW0603
    _get_repeatability_estimate = getter
    logger.info("repeatability_estimate_routes_initialized")


class RepeatabilityContribution(BaseModel):
    joint_name: str
    actuator: str
    source: str
    contribution_mm: float
    resolution_rad: float | None = None
    jacobian_mm_per_rad: float | None = None


class RepeatabilityEstimateResponse(BaseModel):
    work_product_id: str
    target_part: str
    requirement_mm: float
    worst_case_repeatability_mm: float
    passes: bool
    contributions: list[RepeatabilityContribution]
    warnings: list[str]


@router.get("/repeatability-estimate", response_model=RepeatabilityEstimateResponse)
async def get_repeatability_estimate(
    work_product_id: str = Query(...),
    requirement_mm: float = Query(0.5),
    end_effector_part: str | None = Query(None),
) -> RepeatabilityEstimateResponse:
    if _get_repeatability_estimate is None:
        raise HTTPException(status_code=503, detail="repeatability estimator not configured")
    with tracer.start_as_current_span("controls.get_repeatability_estimate") as span:
        span.set_attribute("controls.work_product_id", work_product_id)
        try:
            result = await _get_repeatability_estimate(
                work_product_id=work_product_id,
                requirement_mm=requirement_mm,
                end_effector_part=end_effector_part,
            )
        except ValueError as exc:
            span.record_exception(exc)
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 -- surface a clean 502 with the cause
            logger.warning("repeatability_estimate_get_failed", error=str(exc))
            span.record_exception(exc)
            raise HTTPException(
                status_code=502, detail=f"repeatability estimate failed: {exc}"
            ) from exc
        return RepeatabilityEstimateResponse(**result)
