"""Concept parts from the Create stage: split the 3D concept, map it, redo one part.

* ``GET /v1/twin/projects/{project_id}/concept-parts``: the accepted 3D
  concept, the proposed revisions beside it, the components to map parts to,
  the jobs still running and the hourly spend count.
* ``POST .../concept-parts/segment``: start ``twin.segment_concept_mesh`` as a
  background job on the accepted concept (or the one named).
* ``POST .../concept-parts/{node_id}/mapping``: a person confirms or corrects
  the proposed part to component mapping (free, immediate).
* ``POST .../concept-parts/{node_id}/regenerate``: start
  ``twin.regenerate_concept_part`` for one component (text or an image).
* ``POST .../concept-parts/{node_id}/complete``: start
  ``twin.complete_concept_part`` (``quick_cap`` seals, ``ai_completion``
  completes).
* ``POST .../concept-parts/{node_id}/decision``: accept a proposed revision
  (the previous accepted one is superseded) or reject it.

The paid steps follow the concept mesh rules: an agent caller
(``X-MetaForge-Surface: agent``) is refused (agents call the held tools);
past the project's hourly mesh cap the request answers 409
``spend_confirmation_required`` until the person confirms (``confirm_spend``).
The mapping and the decision are a person's, so agents are refused there too.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from api_gateway.auth.approver import approver_from_request
from api_gateway.twin.concept_mesh_routes import (
    SpendConfirmationRequiredError,
    SpendInfo,
    _created,
    _uuid,
)
from api_gateway.twin.concept_parts import (
    CONCEPT_FAMILY_TOOLS,
    ConceptPartsError,
    ConceptPartsService,
)
from api_gateway.twin.mesh_generation import (
    MAX_PROMPT_CHARS,
    MeshGenerationError,
    MeshGenerationUnavailableError,
)
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.concept_parts_routes")

router = APIRouter(prefix="/v1/twin", tags=["twin"])

SURFACE_HEADER = "X-MetaForge-Surface"
TARGET_KEY = "create:concept-parts"
EXPECTED_SECONDS = 150.0

_service: ConceptPartsService | None = None
_job_runner: Any = None


def set_service(service: ConceptPartsService | None) -> None:
    """Install the service (tests); ``None`` builds the default per call."""
    global _service  # noqa: PLW0603
    _service = service


def set_job_runner(runner: Any) -> None:
    global _job_runner  # noqa: PLW0603
    _job_runner = runner


def _get_service() -> ConceptPartsService:
    if _service is not None:
        return _service
    from api_gateway.twin.concept_mesh_routes import _project_backend, _twin
    from api_gateway.twin.concept_parts import make_concept_parts_service

    return make_concept_parts_service(_twin(), _project_backend())


def _get_runner() -> Any:
    if _job_runner is not None:
        return _job_runner
    from tool_registry.jobs import default_job_runner

    return default_job_runner()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class SegmentRequest(BaseModel):
    #: The accepted 3D concept to split (def: the project's accepted one).
    node_id: str | None = Field(None, max_length=64)
    model: Literal["v1.0-20250506", "v2.0-20260430"] = "v1.0-20250506"
    granularity: Literal["simple", "balanced", "detailed"] | None = None
    split_by_connectivity: bool | None = None
    confirm_spend: bool = False


class PartAssignment(BaseModel):
    part: str = Field(min_length=1, max_length=200)
    #: The component the part is, or ``None`` for "not a component".
    component_id: str | None = Field(None, max_length=200)


class MappingRequest(BaseModel):
    assignments: list[PartAssignment] = Field(default_factory=list, max_length=200)
    #: Confirm every confident proposal not answered above.
    accept_proposals: bool = False


class RegenerateRequest(BaseModel):
    component_id: str = Field(min_length=1, max_length=200)
    source: Literal["text", "image"]
    prompt: str | None = Field(None, max_length=MAX_PROMPT_CHARS)
    image_node_id: str | None = Field(None, max_length=64)
    #: An edited or cropped picture of the part, base64 (PNG, JPEG or WebP).
    image_base64: str | None = Field(None, max_length=14_000_000)
    image_mime: Literal["image/png", "image/jpeg", "image/webp"] | None = None
    confirm_spend: bool = False


class CompleteRequest(BaseModel):
    component_id: str = Field(min_length=1, max_length=200)
    completion_mode: Literal["quick_cap", "ai_completion"] = "quick_cap"
    confirm_spend: bool = False


class ConceptDecisionRequest(BaseModel):
    decision: Literal["accept", "reject"]


class ConceptPartsStarted(BaseModel):
    job_id: str
    status: str
    operation_id: str
    tool: str
    reused: bool = False
    expected_seconds: float | None = None
    spend: SpendInfo
    result: dict[str, Any] | None = None
    error: str | None = None
    authority: str = "illustrative"


class ConceptPartsState(BaseModel):
    current: dict[str, Any] | None = None
    proposals: list[dict[str, Any]] = Field(default_factory=list)
    components: list[dict[str, Any]] = Field(default_factory=list)
    active_jobs: list[dict[str, Any]] = Field(default_factory=list)
    spend: SpendInfo
    authority: str = "illustrative"


# ---------------------------------------------------------------------------
# Spend
# ---------------------------------------------------------------------------


def _active(runner: Any, project_id: str) -> list[Any]:
    store = getattr(runner, "store", None)
    if store is None:
        return []
    try:
        return list(store.active_for_target(project_id, TARGET_KEY))
    except Exception:  # noqa: BLE001 - a runner without a store has no record
        return []


async def spend_info(project_id: str, tool: str, runner: Any) -> SpendInfo:
    """Paid mesh steps of the project in the last hour (all of them), the cap and the policy."""
    from api_gateway.approvals.policy import effective_mode, get_policy_store
    from api_gateway.approvals.tool_write_policy import hourly_cap
    from api_gateway.twin.concept_mesh_routes import _of_type, _twin
    from mcp_core.guardrails import policy_may_approve

    since = datetime.now(UTC) - timedelta(hours=1)
    renders = await _of_type(_twin(), project_id, "render")
    used = sum(
        1
        for wp in renders
        if (wp.metadata or {}).get("generated_by") in CONCEPT_FAMILY_TOOLS and _created(wp) >= since
    )
    used += len(_active(runner, project_id))
    try:
        mode = effective_mode(await get_policy_store().get(project_id))
    except Exception as exc:  # noqa: BLE001 - no policy store: nothing approved by policy
        logger.info("concept_parts_policy_unavailable", project_id=project_id, error=str(exc))
        mode = "off"
    return SpendInfo(
        used_last_hour=used,
        cap=hourly_cap(tool),
        policy_mode=mode,
        policy_would_approve=policy_may_approve(tool, mode),
    )


def check_spend(info: SpendInfo, tool: str, *, confirmed: bool) -> None:
    from api_gateway.approvals.tool_write_policy import SPEND_CAPS
    from mcp_core.guardrails import never_auto_approved

    if confirmed:
        return
    if never_auto_approved(tool):
        raise SpendConfirmationRequiredError("This is a paid step a person confirms every time.")
    if info.cap is not None and info.used_last_hour >= info.cap:
        raise SpendConfirmationRequiredError(
            f"{info.used_last_hour} paid 3D concept steps ran in this project in the last hour "
            f"(cap {info.cap}, {SPEND_CAPS[tool][0]}). Each is a paid generation. Confirm to "
            "run another."
        )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def _refuse_agent(request: Request, what: str) -> None:
    if (request.headers.get(SURFACE_HEADER) or "").strip().lower() == "agent":
        raise HTTPException(status_code=403, detail=what)


def _http(exc: Exception) -> HTTPException:
    if isinstance(exc, SpendConfirmationRequiredError):
        return HTTPException(
            status_code=409, detail={"code": "spend_confirmation_required", "message": str(exc)}
        )
    if isinstance(exc, MeshGenerationUnavailableError):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, ConceptPartsError):
        return HTTPException(status_code=exc.status, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


async def _in_project(service: ConceptPartsService, project_id: str, node_id: str) -> None:
    wp = await service._get(node_id)
    if str(getattr(wp, "project_id", "") or "") != str(_uuid(project_id)):
        raise ConceptPartsError("no such 3D concept in this project", 404)


async def _start(
    project_id: str,
    tool: str,
    method: str,
    call: dict[str, Any],
    *,
    confirmed: bool,
    actor: str,
) -> ConceptPartsStarted:
    from tool_registry.jobs import JobRunner

    service = _get_service()
    runner = _get_runner()
    info = await spend_info(project_id, tool, runner)
    check_spend(info, tool, confirmed=confirmed)
    await getattr(service, method)(**call, validate_only=True)
    request_context = {
        "surface": "create",
        "requested_by": actor,
        "approved_by": f"person:{actor}",
        "policy_mode": info.policy_mode,
        "policy_would_approve": info.policy_would_approve,
        "spend_confirmed": confirmed,
        "used_last_hour": info.used_last_hour,
        "cap": info.cap,
    }

    async def work() -> dict[str, Any]:
        result: dict[str, Any] = await getattr(service, method)(
            **call, request_context=request_context
        )
        return result

    job, reused = await runner.start(
        tool,
        {**{k: v for k, v in call.items() if k != "image_base64"}, "surface": "create"},
        work,
        session_id=f"create-{project_id}",
        project_id=project_id,
        expected_seconds=EXPECTED_SECONDS,
        target_key=TARGET_KEY,
    )
    logger.info(
        "concept_parts_route_started",
        project_id=project_id,
        tool=tool,
        job_id=job.job_id,
        reused=reused,
        spend_confirmed=confirmed,
    )
    reply = JobRunner.reply(job, reused)
    return ConceptPartsStarted(
        job_id=reply["job_id"],
        status=reply["status"],
        operation_id=f"job:{job.job_id}",
        tool=tool,
        reused=reused,
        expected_seconds=reply.get("expected_seconds"),
        spend=info,
        result=reply.get("result"),
        error=reply.get("error"),
    )


@router.get("/projects/{project_id}/concept-parts", response_model=ConceptPartsState)
async def concept_parts_state(project_id: str) -> ConceptPartsState:
    """The accepted 3D concept, the proposals beside it and what to map parts to."""
    with tracer.start_as_current_span("concept_parts_routes.state") as span:
        span.set_attribute("project_id", project_id)
        _uuid(project_id)
        state = await _get_service().state(project_id)
        runner = _get_runner()
        return ConceptPartsState(
            **state,
            active_jobs=[j.as_dict() for j in _active(runner, project_id)],
            spend=await spend_info(project_id, "twin.segment_concept_mesh", runner),
        )


@router.post("/projects/{project_id}/concept-parts/segment", response_model=ConceptPartsStarted)
async def segment_concept(
    project_id: str, body: SegmentRequest, request: Request
) -> ConceptPartsStarted:
    """Split the accepted 3D concept into parts (a paid job). 409 asks to confirm spend."""
    _refuse_agent(request, "agents split a 3D concept through twin.segment_concept_mesh")
    actor = str(approver_from_request(request).actor_id)
    with tracer.start_as_current_span("concept_parts_routes.segment") as span:
        span.set_attribute("project_id", project_id)
        try:
            service = _get_service()
            node_id = body.node_id
            if node_id:
                await _in_project(service, project_id, node_id)
            else:
                current = (await service.state(project_id))["current"]
                if current is None:
                    raise ConceptPartsError("there is no 3D concept to split yet", 409)
                node_id = current["node_id"]
            return await _start(
                project_id,
                "twin.segment_concept_mesh",
                "segment",
                {
                    "node_id": node_id,
                    "model": body.model,
                    "granularity": body.granularity,
                    "split_by_connectivity": body.split_by_connectivity,
                },
                confirmed=body.confirm_spend,
                actor=actor,
            )
        except (
            SpendConfirmationRequiredError,
            MeshGenerationError,
            ConceptPartsError,
            ValueError,
        ) as exc:
            span.record_exception(exc)
            raise _http(exc) from exc


@router.post("/projects/{project_id}/concept-parts/{node_id}/mapping")
async def confirm_part_mapping(
    project_id: str, node_id: str, body: MappingRequest, request: Request
) -> dict[str, Any]:
    """A person confirms or corrects which component each part is."""
    _refuse_agent(request, "a person confirms the part mapping")
    actor = str(approver_from_request(request).actor_id)
    with tracer.start_as_current_span("concept_parts_routes.mapping") as span:
        span.set_attribute("project_id", project_id)
        try:
            service = _get_service()
            await _in_project(service, project_id, node_id)
            return await service.confirm_mapping(
                node_id=node_id,
                assignments={a.part: a.component_id for a in body.assignments},
                accept_proposals=body.accept_proposals,
                actor=f"person:{actor}",
            )
        except (ConceptPartsError, ValueError) as exc:
            span.record_exception(exc)
            raise _http(exc) from exc


@router.post(
    "/projects/{project_id}/concept-parts/{node_id}/regenerate",
    response_model=ConceptPartsStarted,
)
async def regenerate_part(
    project_id: str, node_id: str, body: RegenerateRequest, request: Request
) -> ConceptPartsStarted:
    """Redo one part (text or an image) and swap only it (a paid job)."""
    _refuse_agent(request, "agents redo a part through twin.regenerate_concept_part")
    actor = str(approver_from_request(request).actor_id)
    with tracer.start_as_current_span("concept_parts_routes.regenerate") as span:
        span.set_attribute("project_id", project_id)
        try:
            await _in_project(_get_service(), project_id, node_id)
            return await _start(
                project_id,
                "twin.regenerate_concept_part",
                "regenerate_part",
                {
                    "node_id": node_id,
                    "component_id": body.component_id,
                    "source": body.source,
                    "prompt": body.prompt,
                    "image_node_id": body.image_node_id,
                    "image_base64": body.image_base64,
                    "image_mime": body.image_mime,
                },
                confirmed=body.confirm_spend,
                actor=actor,
            )
        except (
            SpendConfirmationRequiredError,
            MeshGenerationError,
            ConceptPartsError,
            ValueError,
        ) as exc:
            span.record_exception(exc)
            raise _http(exc) from exc


@router.post(
    "/projects/{project_id}/concept-parts/{node_id}/complete",
    response_model=ConceptPartsStarted,
)
async def complete_part(
    project_id: str, node_id: str, body: CompleteRequest, request: Request
) -> ConceptPartsStarted:
    """Seal or complete one part with the provider (a paid job)."""
    _refuse_agent(request, "agents seal a part through twin.complete_concept_part")
    actor = str(approver_from_request(request).actor_id)
    with tracer.start_as_current_span("concept_parts_routes.complete") as span:
        span.set_attribute("project_id", project_id)
        try:
            await _in_project(_get_service(), project_id, node_id)
            return await _start(
                project_id,
                "twin.complete_concept_part",
                "complete_part",
                {
                    "node_id": node_id,
                    "component_id": body.component_id,
                    "completion_mode": body.completion_mode,
                },
                confirmed=body.confirm_spend,
                actor=actor,
            )
        except (
            SpendConfirmationRequiredError,
            MeshGenerationError,
            ConceptPartsError,
            ValueError,
        ) as exc:
            span.record_exception(exc)
            raise _http(exc) from exc


@router.post("/projects/{project_id}/concept-parts/{node_id}/decision")
async def decide_revision(
    project_id: str, node_id: str, body: ConceptDecisionRequest, request: Request
) -> dict[str, Any]:
    """Accept a proposed 3D concept revision (the previous one is superseded) or reject it."""
    _refuse_agent(request, "a person accepts or rejects a 3D concept revision")
    actor = str(approver_from_request(request).actor_id)
    with tracer.start_as_current_span("concept_parts_routes.decision") as span:
        span.set_attribute("project_id", project_id)
        try:
            service = _get_service()
            await _in_project(service, project_id, node_id)
            return await service.decide(
                node_id=node_id, decision=body.decision, actor=f"person:{actor}"
            )
        except (ConceptPartsError, ValueError) as exc:
            span.record_exception(exc)
            raise _http(exc) from exc
