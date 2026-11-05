"""Make a 3D concept from the Create stage.

* ``POST /v1/twin/projects/{project_id}/concept-mesh`` starts
  ``twin.generate_concept_mesh`` as the existing background job. The source
  is the accepted generated concept (the ``generated_sketch`` baseline), else
  the newest concept image made by ``twin.render_concept_image``, else the
  brief in words. The reply names the job and its operation, whose
  ``queued``, ``started`` and ``completed`` events stream on the project's
  operation stream.
* ``GET .../concept-mesh`` returns the newest 3D concept of the project, the
  job still running (if any), the source the next request would use and the
  hourly spend count.

The mesh is an illustrative ``render`` in GLB ("AI 3D reference model,
illustrative only"), never a ``cad_model``.

A person starts this from the dashboard. Agents call
``twin.generate_concept_mesh``, which the guardrails hold for approval; an
agent caller (``X-MetaForge-Surface: agent``) is refused here. The paid-tool
rules hold as for the held tool: a tool on the never-approved list always
waits for a person, and past the project's hourly mesh cap
(``METAFORGE_AUTO_APPROVE_MESH_PER_HOUR``) the request answers 409
``spend_confirmation_required`` and runs only when the person confirms
(``confirm_spend``). The record keeps who asked, the policy mode and whether
the policy alone would have approved the call.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from api_gateway.auth.approver import approver_from_request
from api_gateway.twin.concept_mesh import (
    LABEL,
    SOURCE_CONCEPT_IMAGE,
    SOURCE_GENERATED_SKETCH,
    TOOL,
    source_kind,
)
from api_gateway.twin.mesh_generation import (
    MAX_PROMPT_CHARS,
    MeshGenerationUnavailableError,
)
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.concept_mesh_routes")

router = APIRouter(prefix="/v1/twin", tags=["twin"])

SURFACE_HEADER = "X-MetaForge-Surface"
#: The job target, so a GET can find the job still running for a project.
TARGET_KEY = "create:concept-mesh"
#: About how long Tripo takes (the tool's own estimate).
EXPECTED_SECONDS = 150.0
#: The brief items that describe the product, in the order they are used.
BRIEF_TEXT_KEYS = ("intended_use", "appearance", "size_and_fit", "materials")

SourceChoice = Literal["auto", "generated_sketch", "concept_image", "text"]

#: ``(project_id) -> text``: the Create brief in words; injectable for tests.
BriefText = Callable[[str], Awaitable[str]]

_generator: Any = None
_job_runner: Any = None
_brief_text: BriefText | None = None


def _twin() -> Any:
    from api_gateway.twin.routes import get_twin

    return get_twin()


def _project_backend() -> Any:
    try:
        from api_gateway.projects.routes import get_project_backend

        return get_project_backend()
    except Exception as exc:  # noqa: BLE001 - the link is best-effort
        logger.info("concept_mesh_project_backend_unavailable", error=str(exc))
        return None


def set_generator(generator: Any) -> None:
    """Install the mesh recorder (tests); ``None`` builds the default per call."""
    global _generator  # noqa: PLW0603
    _generator = generator


def set_job_runner(runner: Any) -> None:
    global _job_runner  # noqa: PLW0603
    _job_runner = runner


def set_brief_text(reader: BriefText | None) -> None:
    global _brief_text  # noqa: PLW0603
    _brief_text = reader


def _get_generator() -> Any:
    if _generator is not None:
        return _generator
    from api_gateway.twin.concept_mesh import make_concept_mesh_recorder

    return make_concept_mesh_recorder(_twin(), _project_backend())


def _get_runner() -> Any:
    if _job_runner is not None:
        return _job_runner
    from tool_registry.jobs import default_job_runner

    return default_job_runner()


async def _default_brief_text(project_id: str) -> str:
    """The Create brief's descriptive items, joined, for a text-to-model request."""
    from api_gateway.create.routes import get_create_store

    creation = get_create_store().get(project_id)
    parts: list[str] = []
    for key in BRIEF_TEXT_KEYS:
        item = creation.brief.items.get(key)
        if item is not None and item.status != "missing" and item.value.strip():
            parts.append(item.value.strip())
    return ". ".join(parts)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ConceptMeshRequest(BaseModel):
    #: Where the mesh comes from. ``auto`` takes the accepted generated
    #: concept, else the newest concept image, else the brief in words.
    source: SourceChoice = "auto"
    #: A specific accepted concept or concept image of this project.
    image_node_id: str | None = Field(None, max_length=64)
    #: Words for a text-to-model request (the brief is used when omitted).
    prompt: str | None = Field(None, max_length=MAX_PROMPT_CHARS)
    #: Go past the hourly mesh cap (a person's explicit choice).
    confirm_spend: bool = False


class ConceptMeshSource(BaseModel):
    kind: Literal["generated_sketch", "concept_image", "text"]
    node_id: str | None = None
    item_ref: str | None = None
    name: str | None = None
    prompt: str | None = None


class SpendInfo(BaseModel):
    used_last_hour: int
    cap: int | None
    policy_mode: str
    #: The project's policy alone would have approved this tool call.
    policy_would_approve: bool


class ConceptMeshStarted(BaseModel):
    job_id: str
    status: str
    operation_id: str
    reused: bool = False
    expected_seconds: float | None = None
    source: ConceptMeshSource
    spend: SpendInfo
    result: dict[str, Any] | None = None
    error: str | None = None
    authority: str = "illustrative"
    label: str = LABEL


class ConceptMeshState(BaseModel):
    #: The newest 3D concept of the project, if any.
    latest: dict[str, Any] | None = None
    #: The job still running, if any.
    active_job: dict[str, Any] | None = None
    #: What a request with ``source: auto`` would use now (``None``: nothing yet).
    next_source: ConceptMeshSource | None = None
    spend: SpendInfo
    authority: str = "illustrative"
    label: str = LABEL


# ---------------------------------------------------------------------------
# The work behind the routes
# ---------------------------------------------------------------------------


class SpendConfirmationRequiredError(Exception):
    """A person must confirm this paid call before it runs."""


@dataclass
class ResolvedSource:
    kind: str
    node_id: str | None = None
    item_ref: str | None = None
    name: str | None = None
    prompt: str | None = None

    def view(self) -> ConceptMeshSource:
        return ConceptMeshSource.model_validate(self.__dict__)


def _uuid(project_id: str) -> UUID:
    try:
        return UUID(str(project_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{project_id!r} is not a project id") from exc


def _created(wp: Any) -> datetime:
    created = getattr(wp, "created_at", None)
    if not isinstance(created, datetime):
        return datetime.min.replace(tzinfo=UTC)
    return created if created.tzinfo else created.replace(tzinfo=UTC)


async def _of_type(twin: Any, project_id: str, wp_type: str) -> list[Any]:
    from twin_core.models.enums import WorkProductType

    return list(
        await twin.list_work_products(
            work_product_type=WorkProductType(wp_type), project_id=_uuid(project_id)
        )
    )


async def _meshes(twin: Any, project_id: str) -> list[Any]:
    renders = await _of_type(twin, project_id, "render")
    made = [wp for wp in renders if (wp.metadata or {}).get("generated_by") == TOOL]
    return sorted(made, key=_created, reverse=True)


def _ref_of(wp: Any) -> str | None:
    meta = wp.metadata or {}
    if meta.get("item_key") and meta.get("item_revision"):
        return f"{meta['item_key']}@{meta['item_revision']}"
    return None


async def _accepted_concept(twin: Any, project_id: str) -> Any:
    """The head of the main accepted concept line (not a branch), if any."""
    from api_gateway.twin.sketch_generation import SketchGenerationService

    overview = await SketchGenerationService(twin).overview(project_id)
    for line in overview.get("lines") or []:
        if "#branch-" in str(line.get("baseline_for") or ""):
            continue
        head = line.get("head")
        if head and head.get("node_id"):
            return await twin.get_work_product(UUID(str(head["node_id"])))
    return None


async def _newest_concept_image(twin: Any, project_id: str) -> Any:
    renders = await _of_type(twin, project_id, "render")
    images = [wp for wp in renders if source_kind(wp) == SOURCE_CONCEPT_IMAGE]
    return max(images, key=_created) if images else None


def _from_wp(wp: Any) -> ResolvedSource:
    return ResolvedSource(
        kind=str(source_kind(wp)), node_id=str(wp.id), item_ref=_ref_of(wp), name=wp.name
    )


async def resolve_source(
    twin: Any, project_id: str, body: ConceptMeshRequest, brief_text: BriefText
) -> ResolvedSource | None:
    """The source a request uses, or ``None`` when there is nothing to make it from."""
    if body.image_node_id:
        try:
            wp = await twin.get_work_product(UUID(body.image_node_id.strip()))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="image_node_id is not a node id") from exc
        if wp is None or str(getattr(wp, "project_id", "") or "") != str(_uuid(project_id)):
            raise HTTPException(status_code=404, detail="no such concept in this project")
        if source_kind(wp) is None:
            raise HTTPException(
                status_code=422,
                detail="only an accepted concept or a concept image can become a 3D concept",
            )
        return _from_wp(wp)
    if body.source in ("auto", SOURCE_GENERATED_SKETCH):
        accepted = await _accepted_concept(twin, project_id)
        if accepted is not None:
            return _from_wp(accepted)
        if body.source == SOURCE_GENERATED_SKETCH:
            raise HTTPException(status_code=409, detail="no concept has been accepted yet")
    if body.source in ("auto", SOURCE_CONCEPT_IMAGE):
        image = await _newest_concept_image(twin, project_id)
        if image is not None:
            return _from_wp(image)
        if body.source == SOURCE_CONCEPT_IMAGE:
            raise HTTPException(status_code=409, detail="this project has no concept image yet")
    text = (body.prompt or "").strip()
    if not text:
        try:
            text = (await brief_text(project_id)).strip()
        except Exception as exc:  # noqa: BLE001 - no brief, no text
            logger.info("concept_mesh_brief_unavailable", project_id=project_id, error=str(exc))
            text = ""
    if not text:
        return None
    return ResolvedSource(kind="text", prompt=text[:MAX_PROMPT_CHARS])


async def spend_info(twin: Any, project_id: str, runner: Any) -> SpendInfo:
    """Meshes made in the project in the last hour, the cap and the policy's view."""
    from api_gateway.approvals.policy import effective_mode, get_policy_store
    from api_gateway.approvals.tool_write_policy import hourly_cap
    from mcp_core.guardrails import policy_may_approve

    since = datetime.now(UTC) - timedelta(hours=1)
    used = sum(1 for wp in await _meshes(twin, project_id) if _created(wp) >= since)
    used += len(_active_jobs(runner, project_id))
    try:
        mode = effective_mode(await get_policy_store().get(project_id))
    except Exception as exc:  # noqa: BLE001 - no policy store: nothing is approved by policy
        logger.info("concept_mesh_policy_unavailable", project_id=project_id, error=str(exc))
        mode = "off"
    return SpendInfo(
        used_last_hour=used,
        cap=hourly_cap(TOOL),
        policy_mode=mode,
        policy_would_approve=policy_may_approve(TOOL, mode),
    )


def check_spend(info: SpendInfo, *, confirmed: bool) -> None:
    """Refuse a paid call a person has to confirm first.

    A tool on the never-approved list always needs the confirmation. So does
    one past the project's hourly cap, as it would be held for a person if an
    agent asked.
    """
    from api_gateway.approvals.tool_write_policy import SPEND_CAPS
    from mcp_core.guardrails import never_auto_approved

    if confirmed:
        return
    if never_auto_approved(TOOL):
        raise SpendConfirmationRequiredError(
            "A 3D concept is a paid generation a person confirms every time."
        )
    if info.cap is not None and info.used_last_hour >= info.cap:
        env = SPEND_CAPS[TOOL][0]
        raise SpendConfirmationRequiredError(
            f"{info.used_last_hour} 3D concepts were made in this project in the last hour "
            f"(cap {info.cap}, {env}). Each is a paid generation of about two and a half "
            "minutes. Confirm to make another."
        )


def _active_jobs(runner: Any, project_id: str) -> list[Any]:
    store = getattr(runner, "store", None)
    if store is None:
        return []
    try:
        return list(store.active_for_target(project_id, TARGET_KEY))
    except Exception:  # noqa: BLE001 - a runner without a store has no record
        return []


def mesh_summary(wp: Any) -> dict[str, Any]:
    """The JSON shape of one 3D concept for the dashboard."""
    meta = wp.metadata or {}
    keys = (
        "source_kind",
        "source_image_node_id",
        "source_item_ref",
        "prompt",
        "provider",
        "model_version",
        "task_id",
        "fit_to_envelope",
        "scale_factor",
        "envelope_mm",
        "bbox_m",
        "proportion_error_pct",
        "manufacturing_note",
        "request",
    )
    out: dict[str, Any] = {k: meta.get(k) for k in keys if meta.get(k) is not None}
    out.update(
        {
            "node_id": str(wp.id),
            "name": wp.name,
            "created_at": _created(wp).isoformat(),
            "authority": meta.get("authority") or "illustrative",
            "label": meta.get("illustrative_label") or LABEL,
        }
    )
    return out


async def start_mesh(
    project_id: str, body: ConceptMeshRequest, *, actor: str
) -> ConceptMeshStarted:
    """Resolve the source, apply the spend rules and start the job."""
    from tool_registry.jobs import JobRunner

    twin = _twin()
    runner = _get_runner()
    source = await resolve_source(twin, project_id, body, _brief_text or _default_brief_text)
    if source is None:
        raise HTTPException(
            status_code=422,
            detail=(
                "There is nothing to make a 3D concept from yet. Describe the product, "
                "or accept a concept first."
            ),
        )
    info = await spend_info(twin, project_id, runner)
    check_spend(info, confirmed=body.confirm_spend)
    generate = _get_generator()
    call: dict[str, Any] = {"project_id": str(_uuid(project_id))}
    if source.kind == "text":
        call["prompt"] = source.prompt
    else:
        call["image_node_id"] = source.node_id
    # Refuse a call the generator would refuse (no provider, unreadable
    # image) now, not on the job.
    await generate(**call, validate_only=True)
    request_context = {
        "surface": "create",
        "requested_by": actor,
        "approved_by": f"person:{actor}",
        "policy_mode": info.policy_mode,
        "policy_would_approve": info.policy_would_approve,
        "spend_confirmed": body.confirm_spend,
        "used_last_hour": info.used_last_hour,
        "cap": info.cap,
        "source_kind": source.kind,
    }

    async def work() -> dict[str, Any]:
        result: dict[str, Any] = await generate(**call, request_context=request_context)
        return result

    job, reused = await runner.start(
        TOOL,
        {**call, "surface": "create"},
        work,
        session_id=f"create-{project_id}",
        project_id=project_id,
        expected_seconds=EXPECTED_SECONDS,
        target_key=TARGET_KEY,
    )
    logger.info(
        "concept_mesh_route_started",
        project_id=project_id,
        job_id=job.job_id,
        reused=reused,
        source_kind=source.kind,
        source_node_id=source.node_id,
        spend_confirmed=body.confirm_spend,
        used_last_hour=info.used_last_hour,
    )
    reply = JobRunner.reply(job, reused)
    return ConceptMeshStarted(
        job_id=reply["job_id"],
        status=reply["status"],
        operation_id=f"job:{job.job_id}",
        reused=reused,
        expected_seconds=reply.get("expected_seconds"),
        source=source.view(),
        spend=info,
        result=reply.get("result"),
        error=reply.get("error"),
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def _refuse_agent(request: Request) -> None:
    if (request.headers.get(SURFACE_HEADER) or "").strip().lower() == "agent":
        raise HTTPException(
            status_code=403,
            detail=(
                "agents make 3D concepts through twin.generate_concept_mesh, "
                "which is held for approval"
            ),
        )


@router.post("/projects/{project_id}/concept-mesh", response_model=ConceptMeshStarted)
async def start_concept_mesh(
    project_id: str, body: ConceptMeshRequest, request: Request
) -> ConceptMeshStarted:
    """Make an illustrative 3D concept (a GLB) as a background job.

    From the accepted concept, else the newest concept image, else the brief
    in words. 409 ``spend_confirmation_required`` when a person must confirm
    the paid call first (``confirm_spend``).
    """
    _refuse_agent(request)
    approver = approver_from_request(request)
    started_at = time.monotonic()
    with tracer.start_as_current_span("concept_mesh_routes.start") as span:
        span.set_attribute("project_id", project_id)
        try:
            reply = await start_mesh(project_id, body, actor=str(approver.actor_id))
        except SpendConfirmationRequiredError as exc:
            span.set_attribute("concept_mesh.spend_confirmation_required", True)
            raise HTTPException(
                status_code=409,
                detail={"code": "spend_confirmation_required", "message": str(exc)},
            ) from exc
        except MeshGenerationUnavailableError as exc:
            span.record_exception(exc)
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ValueError as exc:
            span.record_exception(exc)
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        span.set_attribute("concept_mesh.source_kind", reply.source.kind)
        span.set_attribute("concept_mesh.job_id", reply.job_id)
        logger.info(
            "concept_mesh_route_replied",
            project_id=project_id,
            job_id=reply.job_id,
            elapsed_ms=round((time.monotonic() - started_at) * 1000, 1),
        )
        return reply


@router.get("/projects/{project_id}/concept-mesh", response_model=ConceptMeshState)
async def concept_mesh_state(project_id: str) -> ConceptMeshState:
    """The newest 3D concept, the job still running and what the next one would use."""
    with tracer.start_as_current_span("concept_mesh_routes.state") as span:
        span.set_attribute("project_id", project_id)
        twin = _twin()
        runner = _get_runner()
        meshes = await _meshes(twin, project_id)
        active = _active_jobs(runner, project_id)
        source = await resolve_source(
            twin, project_id, ConceptMeshRequest(), _brief_text or _default_brief_text
        )
        return ConceptMeshState(
            latest=mesh_summary(meshes[0]) if meshes else None,
            active_job=active[-1].as_dict() if active else None,
            next_source=source.view() if source else None,
            spend=await spend_info(twin, project_id, runner),
        )
