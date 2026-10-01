"""BOM supply-chain risk API (FORGE-268, gap G-C4).

``GET /v1/bom/risk`` wires the dashboard to the real
``api_gateway.twin.bom_risk.make_bom_risk_scorer`` evaluator -- same "thin
REST wrapper over an injected evaluator closure" pattern established by
``api_gateway/dfm/routes.py`` (FORGE-273) and
``api_gateway/manufacture/routes.py`` (FORGE-294).
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Query

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.bom.risk")

router = APIRouter(prefix="/v1/bom", tags=["bom"])

_score_risk: Any = None


def init_bom_risk_scorer(scorer: Any) -> None:
    """Bind the real ``score_project_bom_risk(project_id)`` closure (built
    in ``api_gateway/server.py`` from ``make_bom_risk_scorer``)."""
    global _score_risk  # noqa: PLW0603
    _score_risk = scorer
    logger.info("bom_risk_scorer_initialized")


@router.get("/risk")
async def get_bom_risk(project_id: str = Query(...)) -> dict[str, Any]:
    if _score_risk is None:
        raise HTTPException(status_code=503, detail="BOM risk scoring not configured")
    with tracer.start_as_current_span("bom.risk_route") as span:
        span.set_attribute("bom_risk.project_id", project_id)
        try:
            return await _score_risk(project_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 -- surface a clean 502 with the cause
            logger.warning("bom_risk_route_failed", project_id=project_id, error=str(exc))
            span.record_exception(exc)
            raise HTTPException(status_code=502, detail=f"BOM risk scoring failed: {exc}") from exc
