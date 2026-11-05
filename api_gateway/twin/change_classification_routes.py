"""Change classification and appearance routes.

Under ``/v1/twin/projects/{project_id}``:

- ``POST .../classify-change``: what a request would change (spec section 5),
  how sure, what it is about and how it would be handled. Writes nothing.
- ``GET .../appearance``: the product's and each component's look.
- ``PUT .../appearance``: set a look (colour, finish, render material,
  textures) for one component or the product. Never rebuilds CAD, never
  touches the manufacturing context, never marks anything stale.

The logic is in :mod:`api_gateway.twin.change_classification` and
:mod:`api_gateway.twin.appearance`.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from api_gateway.twin.appearance import (
    AppearanceError,
    AppearanceRecord,
    read_appearance,
    set_appearance,
)
from api_gateway.twin.change_classification import (
    AppearanceValues,
    ChangeClassification,
    classify_change,
)
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.change_classification_routes")

router = APIRouter(prefix="/v1/twin", tags=["twin"])


def _twin() -> Any:
    from api_gateway.twin.routes import get_twin

    return get_twin()


def _check_project_id(project_id: str) -> None:
    try:
        UUID(project_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid project id format") from None


class ClassifyChangeRequest(BaseModel):
    """A request to classify, and the component it is about when one is selected."""

    text: str = Field(min_length=1, max_length=4000)
    component: str | None = Field(None, max_length=200, description="Selected component name")
    component_id: str | None = Field(None, max_length=200, description="Selected node id")
    #: ``None`` follows ``METAFORGE_CHANGE_CLASSIFIER_LLM``; ``false`` keeps to the rules.
    use_llm: bool | None = None


class AppearancePutRequest(BaseModel):
    """A look for one component (by name or node id), or the product when neither is given."""

    component: str | None = Field(None, max_length=200)
    component_id: str | None = Field(None, max_length=200)
    colour: str | None = Field(None, max_length=7, description="sRGB hex, e.g. #c62828")
    colour_name: str | None = Field(None, max_length=60)
    finish: str | None = Field(None, max_length=60)
    render_material: str | None = Field(None, max_length=80)
    texture_refs: list[str] = Field(default_factory=list, max_length=8)


class AppearanceView(BaseModel):
    """The appearance record as a client reads it."""

    project_id: str
    recorded: bool
    #: ``APR-PROJECT@n``, or ``None`` when nothing was set.
    ref: str | None = None
    record: AppearanceRecord


@router.post(
    "/projects/{project_id}/classify-change",
    response_model=ChangeClassification,
)
async def classify_change_route(project_id: str, body: ClassifyChangeRequest) -> Any:
    """Classify a change request before anything changes. Writes nothing."""
    _check_project_id(project_id)
    with tracer.start_as_current_span("twin.change_classification.route") as span:
        span.set_attribute("project_id", project_id)
        result = await classify_change(
            body.text,
            component=body.component,
            component_id=body.component_id,
            use_llm=body.use_llm,
        )
        span.set_attribute("change.class", result.change_class.value)
        return result


async def _view(project_id: str) -> AppearanceView:
    stored = await read_appearance(_twin(), project_id)
    if stored is None:
        return AppearanceView(
            project_id=project_id, recorded=False, record=AppearanceRecord(project_id=project_id)
        )
    return AppearanceView(
        project_id=project_id, recorded=True, ref=stored.ref, record=stored.record
    )


@router.get("/projects/{project_id}/appearance", response_model=AppearanceView)
async def get_appearance(project_id: str) -> Any:
    """The product's and each component's look. ``recorded: false`` when none was set."""
    _check_project_id(project_id)
    with tracer.start_as_current_span("twin.appearance.route_get") as span:
        span.set_attribute("project_id", project_id)
        return await _view(project_id)


@router.put("/projects/{project_id}/appearance", response_model=AppearanceView)
async def put_appearance(project_id: str, body: AppearancePutRequest) -> Any:
    """Set a look. Only the appearance record changes: never CAD or the manufacturing context."""
    _check_project_id(project_id)
    with tracer.start_as_current_span("twin.appearance.route_put") as span:
        span.set_attribute("project_id", project_id)
        try:
            values = AppearanceValues(
                colour=body.colour,
                colour_name=body.colour_name,
                finish=body.finish,
                render_material=body.render_material,
                texture_refs=list(body.texture_refs),
            )
            await set_appearance(
                _twin(),
                project_id,
                values,
                component_name=body.component,
                component_id=body.component_id,
                source="api",
            )
        except AppearanceError as exc:
            span.record_exception(exc)
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
        except ValueError as exc:
            span.record_exception(exc)
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return await _view(project_id)
