"""DFM check API (FORGE-273, gap G-D5).

``POST /v1/dfm/overhang-check`` wires the dashboard's DFM results panel to
the real ``twin.evaluate_overhang_metric`` evaluator
(``api_gateway.twin.dfm_evidence.make_overhang_evidence_recorder``) -- the
first REST-reachable route in this ``evaluate_*``/evidence-recording family
(``twin.evaluate_metric``/``twin.evaluate_thermal_metric`` remain MCP-only
today). This ticket's own Definition of Done requires a real dashboard
interaction, and the dashboard has no MCP client of its own, so this is a
thin REST wrapper over the same evaluator closure the MCP tool calls --
same pattern as ``api_gateway/requirement_intelligence/routes.py``'s
``init_twin`` seam, adapted for an injected callable rather than a twin
instance.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.dfm")

router = APIRouter(prefix="/v1/dfm", tags=["dfm"])

_evaluate_overhang: Any = None


def init_overhang_evaluator(evaluator: Any) -> None:
    """Bind the real ``evaluate_overhang(...)`` closure (built in
    ``api_gateway/server.py`` from ``make_overhang_evidence_recorder``)."""
    global _evaluate_overhang  # noqa: PLW0603
    _evaluate_overhang = evaluator
    logger.info("dfm_overhang_evaluator_initialized")


class OverhangCheckRequest(BaseModel):
    work_product_id: str
    project_id: str | None = None
    mesh_file: str
    build_axis: list[float] | None = None
    threshold_deg: float | None = None


class OverhangFace(BaseModel):
    name: str | None = None
    area_mm2: float | None = None
    normal: list[float]
    tilt_from_vertical_deg: float
    flagged: bool


class OverhangCheckResponse(BaseModel):
    faces: list[OverhangFace]
    flagged_count: int
    total_faces: int
    threshold_deg: float
    build_axis: list[float]
    dfm_pass: bool
    evidence_node_id: str


@router.post("/overhang-check", response_model=OverhangCheckResponse)
async def run_overhang_check(body: OverhangCheckRequest) -> OverhangCheckResponse:
    if _evaluate_overhang is None:
        raise HTTPException(status_code=503, detail="overhang evaluator not configured")
    with tracer.start_as_current_span("dfm.overhang_check") as span:
        span.set_attribute("dfm.work_product_id", body.work_product_id)
        try:
            result = await _evaluate_overhang(
                work_product_id=body.work_product_id,
                project_id=body.project_id,
                mesh_file=body.mesh_file,
                build_axis=body.build_axis,
                threshold_deg=body.threshold_deg,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 -- surface a clean 502 with the cause
            logger.warning("dfm_overhang_check_failed", error=str(exc))
            span.record_exception(exc)
            raise HTTPException(status_code=502, detail=f"overhang check failed: {exc}") from exc
        return OverhangCheckResponse(**result)
