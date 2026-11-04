"""Concept sketch generation routes for the Create stage (FORGE-794).

* ``POST /v1/twin/projects/{project_id}/sketch-generations`` starts a
  generation as a job on its target (text, reference or sketch mode; a region
  edit with a mask). The reply names the job and its operation, whose
  ``queued``, ``started`` and ``completed`` events stream on the project's
  operation stream. A new request on the same target supersedes an older one
  still running; an identical one joins it.
* ``GET .../sketch-generations`` lists the accepted lines (each with its
  history) and recent proposals.
* ``POST .../sketch-generations/{node_id}/accept``, ``/reject`` and
  ``/branch`` decide a proposal; ``GET .../sketch-generations/compare`` puts
  two side by side.

A person starts these from the dashboard. Agents use ``twin.generate_sketch``,
which is held for approval like every paid image tool; an agent caller
(``X-MetaForge-Surface: agent``) is refused here. Past the project's hourly
image cap the request answers 409 ``spend_confirmation_required`` and runs
only when the person confirms (``confirm_spend``).
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from api_gateway.auth.approver import approver_from_request
from api_gateway.twin.image_generation import ImageGenerationUnavailableError
from api_gateway.twin.sketch_generation import (
    GenerateSketchRequest,
    SketchGenerationError,
    SketchGenerationService,
    SpendConfirmationRequiredError,
)
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.sketch_generation_routes")

router = APIRouter(prefix="/v1/twin", tags=["twin"])

SURFACE_HEADER = "X-MetaForge-Surface"

_service: SketchGenerationService | None = None


def _twin() -> Any:
    from api_gateway.twin.routes import get_twin

    return get_twin()


def _project_backend() -> Any:
    try:
        from api_gateway.projects.routes import get_project_backend

        return get_project_backend()
    except Exception as exc:  # noqa: BLE001 - the link is best-effort
        logger.info("sketch_generation_project_backend_unavailable", error=str(exc))
        return None


def get_service() -> SketchGenerationService:
    """The process's service; tests install their own with :func:`set_service`."""
    if _service is not None:
        return _service
    return SketchGenerationService(_twin(), _project_backend())


def set_service(service: SketchGenerationService | None) -> None:
    global _service  # noqa: PLW0603
    _service = service


class GenerationStarted(BaseModel):
    job_id: str
    status: str
    operation_id: str
    mode: str
    target_key: str
    base_revision: str
    reused: bool = False
    expected_seconds: float | None = None
    accepted_baseline: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    authority: str = "illustrative"


class DecisionBody(BaseModel):
    #: The accepted concept the person was looking at; 409 when it moved.
    expected_baseline: str | None = Field(None, max_length=200)


class BranchBody(BaseModel):
    name: str | None = Field(None, max_length=120)


def _http(exc: SketchGenerationError) -> HTTPException:
    if isinstance(exc, SpendConfirmationRequiredError):
        return HTTPException(
            status_code=409,
            detail={"code": "spend_confirmation_required", "message": str(exc)},
        )
    return HTTPException(status_code=exc.status_code, detail=str(exc))


def _refuse_agent(request: Request) -> None:
    if (request.headers.get(SURFACE_HEADER) or "").strip().lower() == "agent":
        raise HTTPException(
            status_code=403,
            detail=(
                "agents generate concepts through twin.generate_sketch, which is held for approval"
            ),
        )


@router.post("/projects/{project_id}/sketch-generations", response_model=GenerationStarted)
async def start_generation(
    project_id: str, body: GenerateSketchRequest, request: Request
) -> GenerationStarted:
    """Start a concept sketch generation as a job on its target.

    The mode follows from the request: a ``sketch_key`` refines that sketch,
    active references condition it, otherwise it is drawn from text. A
    ``mask`` limits it to a region. ``n_variants`` defaults to 1. Each output
    is stored as a proposal; nothing replaces the accepted concept until a
    person accepts one.
    """
    _refuse_agent(request)
    with tracer.start_as_current_span("sketch_generation_routes.start") as span:
        span.set_attribute("project_id", project_id)
        service = get_service()
        try:
            prepared = await service.prepare(project_id, body, surface="create")
            span.set_attribute("sketch_generation.mode", prepared.mode)
            await service.check_spend(prepared)
            reply = await service.start(prepared)
        except SketchGenerationError as exc:
            span.record_exception(exc)
            raise _http(exc) from exc
        except ImageGenerationUnavailableError as exc:
            span.record_exception(exc)
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return GenerationStarted.model_validate(reply)


@router.get("/projects/{project_id}/sketch-generations")
async def list_generations(project_id: str, limit: int = Query(30, ge=1, le=200)) -> dict[str, Any]:
    """Accepted concept lines (head and history) and recent proposals."""
    with tracer.start_as_current_span("sketch_generation_routes.list") as span:
        span.set_attribute("project_id", project_id)
        try:
            return await get_service().overview(project_id, limit=limit)
        except SketchGenerationError as exc:
            raise _http(exc) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/projects/{project_id}/sketch-generations/compare")
async def compare_generations(
    project_id: str,
    a: str = Query(..., description="A generated concept node id"),
    b: str = Query(..., description="Another generated concept node id"),
) -> dict[str, Any]:
    """Two concepts side by side: their records and the share of pixels that differ."""
    with tracer.start_as_current_span("sketch_generation_routes.compare") as span:
        span.set_attribute("project_id", project_id)
        try:
            return await get_service().compare(project_id, a, b)
        except SketchGenerationError as exc:
            raise _http(exc) from exc


@router.post("/projects/{project_id}/sketch-generations/{node_id}/accept")
async def accept_generation(
    project_id: str, node_id: str, body: DecisionBody, request: Request
) -> dict[str, Any]:
    """Make a proposal the accepted concept. The previous one stays in the line's history."""
    _refuse_agent(request)
    approver = approver_from_request(request)
    with tracer.start_as_current_span("sketch_generation_routes.accept") as span:
        span.set_attribute("project_id", project_id)
        try:
            return await get_service().accept(
                project_id,
                node_id,
                expected_baseline=body.expected_baseline,
                actor=str(approver.actor_id),
            )
        except SketchGenerationError as exc:
            span.record_exception(exc)
            raise _http(exc) from exc


@router.post("/projects/{project_id}/sketch-generations/{node_id}/reject")
async def reject_generation(project_id: str, node_id: str, request: Request) -> dict[str, Any]:
    """Set a proposal aside. It stays in the history, marked rejected."""
    approver = approver_from_request(request)
    with tracer.start_as_current_span("sketch_generation_routes.reject") as span:
        span.set_attribute("project_id", project_id)
        try:
            return await get_service().reject(project_id, node_id, actor=str(approver.actor_id))
        except SketchGenerationError as exc:
            raise _http(exc) from exc


@router.post("/projects/{project_id}/sketch-generations/{node_id}/branch")
async def branch_generation(
    project_id: str, node_id: str, body: BranchBody, request: Request
) -> dict[str, Any]:
    """Start a separate accepted line from this concept; the current one is untouched."""
    _refuse_agent(request)
    approver = approver_from_request(request)
    with tracer.start_as_current_span("sketch_generation_routes.branch") as span:
        span.set_attribute("project_id", project_id)
        try:
            return await get_service().branch(
                project_id, node_id, actor=str(approver.actor_id), name=body.name
            )
        except SketchGenerationError as exc:
            raise _http(exc) from exc
