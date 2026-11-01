"""Test plan API (FORGE-298, gap G-I2).

``POST /v1/testplans`` and ``GET /v1/testplans`` wire the dashboard's test
plan page to the real ``twin.generate_test_plan``/list evaluators
(``api_gateway.twin.test_plan``) -- same thin-REST-wrapper-over-an-injected-
closure pattern ``api_gateway/releases/routes.py`` established for
``twin.create_release_package``.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.testplans")

router = APIRouter(prefix="/v1/testplans", tags=["testplans"])

_generate_test_plan: Any = None
_list_test_plan_entries: Any = None


def init_test_plan(generator: Any, lister: Any) -> None:
    """Bind the real ``generate(...)``/``list_entries(...)`` closures (built
    in ``api_gateway/server.py`` from ``make_test_plan_generator``/
    ``make_test_plan_lister``)."""
    global _generate_test_plan, _list_test_plan_entries  # noqa: PLW0603
    _generate_test_plan = generator
    _list_test_plan_entries = lister
    logger.info("test_plan_routes_initialized")


class GenerateTestPlanRequest(BaseModel):
    project_id: str


class TestPlanEntry(BaseModel):
    node_id: str
    requirement_id: str
    step: str
    acceptance_value: str


class GenerateTestPlanResponse(BaseModel):
    project_id: str
    entries: list[TestPlanEntry]


class TestPlanListEntry(BaseModel):
    node_id: str
    requirement_id: str
    step: str
    acceptance_value: str
    created_at: str


class TestPlanListResponse(BaseModel):
    entries: list[TestPlanListEntry]


@router.post("", response_model=GenerateTestPlanResponse)
async def generate_test_plan(body: GenerateTestPlanRequest) -> GenerateTestPlanResponse:
    if _generate_test_plan is None:
        raise HTTPException(status_code=503, detail="test plan generator not configured")
    with tracer.start_as_current_span("testplans.generate") as span:
        span.set_attribute("test_plan.project_id", body.project_id)
        try:
            result = await _generate_test_plan(project_id=body.project_id)
        except Exception as exc:  # noqa: BLE001 -- surface a clean 502 with the cause
            logger.warning("test_plan_generate_failed", error=str(exc))
            span.record_exception(exc)
            raise HTTPException(
                status_code=502, detail=f"test plan generation failed: {exc}"
            ) from exc
        return GenerateTestPlanResponse(
            project_id=result["project_id"],
            entries=[
                TestPlanEntry(
                    node_id=e["node_id"],
                    requirement_id=e["requirement_id"],
                    step=e["step"],
                    acceptance_value=e["acceptance_value"],
                )
                for e in result["entries"]
            ],
        )


@router.get("", response_model=TestPlanListResponse)
async def list_test_plan(project_id: str) -> TestPlanListResponse:
    if _list_test_plan_entries is None:
        raise HTTPException(status_code=503, detail="test plan lister not configured")
    with tracer.start_as_current_span("testplans.list") as span:
        span.set_attribute("test_plan.project_id", project_id)
        entries = await _list_test_plan_entries(project_id=project_id)
        return TestPlanListResponse(
            entries=[
                TestPlanListEntry(
                    node_id=e["node_id"],
                    requirement_id=e["requirement_id"],
                    step=e["step"],
                    acceptance_value=e["acceptance_value"],
                    created_at=e["created_at"],
                )
                for e in entries
            ]
        )
