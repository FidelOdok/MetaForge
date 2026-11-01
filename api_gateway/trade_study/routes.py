"""Concept generation and trade-study API (FORGE-262, gap G-B2).

``GET /v1/trade-study/options`` lists a project's recorded
``concept_option`` entities (already-generic ``twin.list_engineering_entities``,
zero backend changes) for the dashboard's own options-as-columns table.

``POST /v1/trade-study/options`` records one new candidate architecture --
a thin, entity_type-fixed wrapper over the already-generic
``twin.record_engineering_entity``, so the dashboard's own "+ add option"
form doesn't need an MCP client of its own (same precedent as the two
routes below).

``POST /v1/trade-study/select`` is a thin REST wrapper over
``api_gateway.twin.trade_study.make_trade_study_selector`` -- same
precedent as ``POST /v1/design-loop/start``, so the dashboard's "Select
concept" button doesn't need an MCP client of its own.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from observability.tracing import get_tracer
from twin_core.api import InMemoryTwinAPI

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.trade_study.routes")

_twin: InMemoryTwinAPI = InMemoryTwinAPI.create()


def init_twin(twin: object) -> None:
    """Replace the default in-memory twin with the orchestrator's twin."""
    global _twin  # noqa: PLW0603
    _twin = twin  # type: ignore[assignment]
    logger.info("trade_study_twin_initialized", twin_type=type(twin).__name__)


# An injected async ``select(...)`` (built in server.py over
# make_trade_study_selector, composed with the SAME decision_recorder every
# other Decision-producing tool uses). None until the server lifespan wires
# it in -- same seam as design_loop/routes.py's own _design_loop_starter.
_concept_selector: Any = None


def init_concept_selector(selector: Any) -> None:
    global _concept_selector  # noqa: PLW0603
    _concept_selector = selector


# An injected async ``record(...)`` -- the SAME bound
# make_engineering_entity_recorder(twin, project_backend) instance
# bootstrap_tool_registry uses for twin.record_engineering_entity, so a
# concept_option created via this route or via an agent's own MCP call are
# indistinguishable afterward.
_entity_recorder: Any = None


def init_entity_recorder(recorder: Any) -> None:
    global _entity_recorder  # noqa: PLW0603
    _entity_recorder = recorder


router = APIRouter(prefix="/v1/trade-study", tags=["trade-study"])


@router.get("/options")
async def list_concept_options(project_id: str | None = None) -> dict[str, Any]:
    pid = UUID(project_id) if project_id else None
    entities = await _twin.list_engineering_entities(project_id=pid, entity_type="concept_option")
    return {
        "options": [
            {
                "id": str(e.id),
                "title": e.title or e.statement or str(e.id),
                "criteria_scores": e.metadata.get("criteria_scores") or {},
                "evidence_backed_criteria": e.metadata.get("evidence_backed_criteria") or [],
            }
            for e in entities
        ]
    }


class AddConceptOptionRequest(BaseModel):
    title: str
    criteriaScores: dict[str, float]  # noqa: N815 -- dashboard contract is camelCase
    evidenceBackedCriteria: list[str] = []  # noqa: N815
    projectId: str | None = None  # noqa: N815


@router.post("/options")
async def add_concept_option(payload: AddConceptOptionRequest) -> dict[str, Any]:
    if _entity_recorder is None:
        raise HTTPException(status_code=503, detail="concept recording is not available")
    return await _entity_recorder(
        entity_type="concept_option",
        statement=f"candidate architecture: {payload.title}",
        title=payload.title,
        extra={
            "criteria_scores": payload.criteriaScores,
            "evidence_backed_criteria": payload.evidenceBackedCriteria,
        },
        project_id=payload.projectId,
    )


class SelectConceptRequest(BaseModel):
    optionIds: list[str]  # noqa: N815 -- dashboard contract is camelCase
    selectedOptionId: str  # noqa: N815
    weights: dict[str, float]
    title: str
    rationale: str
    projectId: str | None = None  # noqa: N815
    requirementIds: list[str] | None = None  # noqa: N815


@router.post("/select")
async def select_concept(payload: SelectConceptRequest) -> dict[str, Any]:
    if _concept_selector is None:
        raise HTTPException(status_code=503, detail="trade study selection is not available")
    with tracer.start_as_current_span("trade_study.select") as span:
        span.set_attribute("trade_study.option_count", len(payload.optionIds))
        try:
            return await _concept_selector(
                option_ids=payload.optionIds,
                selected_option_id=payload.selectedOptionId,
                weights=payload.weights,
                title=payload.title,
                rationale=payload.rationale,
                project_id=payload.projectId,
                requirement_ids=payload.requirementIds,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
