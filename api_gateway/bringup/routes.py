"""Assembly bring-up checklist API (FORGE-295, gap G-H3).

``POST /v1/bringup`` and ``GET /v1/bringup`` wire the dashboard's bring-up
checklist view to the real ``twin.create_bringup_checklist``/list closures
(``api_gateway.twin.bringup_checklist``) -- same thin-REST-wrapper-over-an-
injected-closure pattern ``api_gateway/testplans/routes.py`` established for
``twin.generate_test_plan``.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.bringup")

router = APIRouter(prefix="/v1/bringup", tags=["bringup"])

_create_bringup_checklist: Any = None
_list_bringup_checklists: Any = None


def init_bringup_checklist(creator: Any, lister: Any) -> None:
    """Bind the real ``create(...)``/``list_checklists(...)`` closures
    (built in ``api_gateway/server.py`` from
    ``make_bringup_checklist_creator``/``make_bringup_checklist_lister``)."""
    global _create_bringup_checklist, _list_bringup_checklists  # noqa: PLW0603
    _create_bringup_checklist = creator
    _list_bringup_checklists = lister
    logger.info("bringup_checklist_routes_initialized")


class CreateBringupChecklistRequest(BaseModel):
    work_product_id: str
    project_id: str | None = None


class BringupStep(BaseModel):
    step_number: int
    joint_name: str
    joint_type: str
    base: str
    follower: str
    instruction: str


class CreateBringupChecklistResponse(BaseModel):
    node_id: str
    statement: str
    steps: list[BringupStep]


class BringupChecklistListEntry(BaseModel):
    node_id: str
    created_at: str
    title: str | None
    statement: str
    steps: list[BringupStep]


class BringupChecklistListResponse(BaseModel):
    entries: list[BringupChecklistListEntry]


@router.post("", response_model=CreateBringupChecklistResponse)
async def create_bringup_checklist(
    body: CreateBringupChecklistRequest,
) -> CreateBringupChecklistResponse:
    if _create_bringup_checklist is None:
        raise HTTPException(status_code=503, detail="bringup checklist creator not configured")
    with tracer.start_as_current_span("bringup.create") as span:
        span.set_attribute("bringup.work_product_id", body.work_product_id)
        try:
            result = await _create_bringup_checklist(
                work_product_id=body.work_product_id, project_id=body.project_id
            )
        except Exception as exc:  # noqa: BLE001 -- surface a clean 502 with the cause
            logger.warning("bringup_checklist_create_failed", error=str(exc))
            span.record_exception(exc)
            raise HTTPException(
                status_code=502, detail=f"bring-up checklist creation failed: {exc}"
            ) from exc
        return CreateBringupChecklistResponse(
            node_id=result["node_id"],
            statement=result["statement"],
            steps=[BringupStep(**s) for s in result["steps"]],
        )


@router.get("", response_model=BringupChecklistListResponse)
async def list_bringup_checklists(work_product_id: str) -> BringupChecklistListResponse:
    if _list_bringup_checklists is None:
        raise HTTPException(status_code=503, detail="bringup checklist lister not configured")
    with tracer.start_as_current_span("bringup.list") as span:
        span.set_attribute("bringup.work_product_id", work_product_id)
        entries = await _list_bringup_checklists(work_product_id=work_product_id)
        return BringupChecklistListResponse(
            entries=[
                BringupChecklistListEntry(
                    node_id=e["node_id"],
                    created_at=e["created_at"],
                    title=e.get("title"),
                    statement=e["statement"],
                    steps=[BringupStep(**s) for s in e["steps"]],
                )
                for e in entries
            ]
        )
