"""The CAD design loop from Create and Engineering.

* ``GET /v1/twin/projects/{project_id}/cad-loops``: every run (newest first,
  or one component's with ``component_id``), the jobs still running and the
  hourly spend count.
* ``POST .../cad-loops``: start ``twin.design_cad_loop`` for a component's
  parametric plan as a background job. Each model call counts against the
  run's budget; past the project's hourly cap the request answers 409
  ``spend_confirmation_required`` until the person confirms (``confirm_spend``).
* ``GET .../cad-loops/{node_id}``: one run with every iteration.
* ``POST .../cad-loops/{node_id}/decision``: a person's ``accept`` (build the
  proposed version through the exact CAD path; the head moves only on a
  validity pass) or ``reject`` (keep the previous one).

An agent caller (``X-MetaForge-Surface: agent``) is refused on the writes:
agents call the held tool, and only a person decides.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from api_gateway.auth.approver import approver_from_request
from api_gateway.twin.cad_design_loop import (
    LOOP_TOOL,
    TARGET_PREFIX,
    CadDesignLoopService,
    CadLoopError,
    CadLoopRequest,
    LoopUnavailableError,
)
from api_gateway.twin.concept_mesh_routes import (
    SpendConfirmationRequiredError,
    SpendInfo,
    _created,
    _uuid,
)
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.cad_design_loop_routes")

router = APIRouter(prefix="/v1/twin", tags=["twin"])

SURFACE_HEADER = "X-MetaForge-Surface"

_service: CadDesignLoopService | None = None
_job_runner: Any = None


def set_service(service: CadDesignLoopService | None) -> None:
    """Install the service (tests); ``None`` uses the gateway's."""
    global _service  # noqa: PLW0603
    _service = service


def set_job_runner(runner: Any) -> None:
    global _job_runner  # noqa: PLW0603
    _job_runner = runner


def _get_service() -> CadDesignLoopService:
    if _service is not None:
        return _service
    from api_gateway.twin.cad_design_loop import (
        get_cad_design_loop_service,
        make_cad_design_loop_service,
    )

    service = get_cad_design_loop_service()
    if service is not None:
        return service
    from api_gateway.twin.routes import get_twin

    return make_cad_design_loop_service(get_twin())


def _get_runner() -> Any:
    if _job_runner is not None:
        return _job_runner
    from tool_registry.jobs import default_job_runner

    return default_job_runner()


class StartCadLoopRequest(CadLoopRequest):
    confirm_spend: bool = False


class CadLoopDecisionRequest(BaseModel):
    decision: Literal["accept", "reject"]


class CadLoopStarted(BaseModel):
    job_id: str | None = None
    status: str
    operation_id: str | None = None
    reused: bool = False
    expected_seconds: float | None = None
    spend: SpendInfo
    result: dict[str, Any] | None = None
    error: str | None = None


class CadLoopRuns(BaseModel):
    runs: list[dict[str, Any]] = Field(default_factory=list)
    active_jobs: list[dict[str, Any]] = Field(default_factory=list)
    spend: SpendInfo
    #: What a run's verdict is worth: a review, never a validation.
    note: str = (
        "Proposals only: the critic's verdict is a review, not a check. A version is "
        "validated only by the exact CAD path after a person accepts it."
    )


def _active(runner: Any, project_id: str) -> list[Any]:
    store = getattr(runner, "store", None)
    if store is None:
        return []
    try:
        return list(store.active_for_tool(project_id, LOOP_TOOL))
    except Exception:  # noqa: BLE001 - a runner without a store has no record
        return []


async def spend_info(project_id: str, runner: Any) -> SpendInfo:
    """Loop runs of the project in the last hour, the cap and the policy."""
    from api_gateway.approvals.policy import effective_mode, get_policy_store
    from api_gateway.approvals.tool_write_policy import hourly_cap
    from api_gateway.twin.concept_mesh_routes import _of_type, _twin
    from mcp_core.guardrails import policy_may_approve

    since = datetime.now(UTC) - timedelta(hours=1)
    runs = await _of_type(_twin(), project_id, "cad_design_loop")
    used = sum(1 for wp in runs if _created(wp) >= since)
    try:
        mode = effective_mode(await get_policy_store().get(project_id))
    except Exception as exc:  # noqa: BLE001 - no policy store: nothing approved by policy
        logger.info("cad_loop_policy_unavailable", project_id=project_id, error=str(exc))
        mode = "off"
    return SpendInfo(
        used_last_hour=used,
        cap=hourly_cap(LOOP_TOOL),
        policy_mode=mode,
        policy_would_approve=policy_may_approve(LOOP_TOOL, mode),
    )


def check_spend(info: SpendInfo, *, confirmed: bool) -> None:
    from api_gateway.approvals.tool_write_policy import SPEND_CAPS

    if confirmed:
        return
    if info.cap is not None and info.used_last_hour >= info.cap:
        raise SpendConfirmationRequiredError(
            f"{info.used_last_hour} automatic CAD refinements ran in this project in the last "
            f"hour (cap {info.cap}, {SPEND_CAPS[LOOP_TOOL][0]}). Each makes paid model calls. "
            "Confirm to run another."
        )


def _refuse_agent(request: Request, what: str) -> None:
    if (request.headers.get(SURFACE_HEADER) or "").strip().lower() == "agent":
        raise HTTPException(status_code=403, detail=what)


def _http(exc: Exception) -> HTTPException:
    if isinstance(exc, SpendConfirmationRequiredError):
        return HTTPException(
            status_code=409, detail={"code": "spend_confirmation_required", "message": str(exc)}
        )
    if isinstance(exc, LoopUnavailableError):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, CadLoopError):
        return HTTPException(status_code=exc.status, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


@router.get("/projects/{project_id}/cad-loops", response_model=CadLoopRuns)
async def list_cad_loops(project_id: str, component_id: str | None = None) -> CadLoopRuns:
    """Every CAD design loop run of the project, newest first."""
    with tracer.start_as_current_span("cad_loop_routes.list") as span:
        span.set_attribute("project_id", project_id)
        _uuid(project_id)
        runner = _get_runner()
        try:
            runs = await _get_service().runs(project_id, component_id=component_id)
        except CadLoopError as exc:
            span.record_exception(exc)
            raise _http(exc) from exc
        return CadLoopRuns(
            runs=runs,
            active_jobs=[j.as_dict() for j in _active(runner, project_id)],
            spend=await spend_info(project_id, runner),
        )


@router.post("/projects/{project_id}/cad-loops", response_model=CadLoopStarted)
async def start_cad_loop(
    project_id: str, body: StartCadLoopRequest, request: Request
) -> CadLoopStarted:
    """Refine a component's model automatically (a paid job). 409 asks to confirm spend."""
    _refuse_agent(request, f"agents start the CAD design loop through {LOOP_TOOL}")
    actor = str(approver_from_request(request).actor_id)
    with tracer.start_as_current_span("cad_loop_routes.start") as span:
        span.set_attribute("project_id", project_id)
        _uuid(project_id)
        runner = _get_runner()
        try:
            info = await spend_info(project_id, runner)
            check_spend(info, confirmed=body.confirm_spend)
            loop_request = CadLoopRequest.model_validate(body.model_dump(exclude={"confirm_spend"}))
            reply = await _get_service().start(
                project_id,
                loop_request,
                session_id=f"create-{project_id}",
                request_context={
                    "surface": "create",
                    "requested_by": actor,
                    "approved_by": f"person:{actor}",
                    "policy_mode": info.policy_mode,
                    "spend_confirmed": body.confirm_spend,
                    "used_last_hour": info.used_last_hour,
                    "cap": info.cap,
                },
            )
        except (SpendConfirmationRequiredError, CadLoopError, LoopUnavailableError) as exc:
            span.record_exception(exc)
            raise _http(exc) from exc
        logger.info(
            "cad_loop_route_started",
            project_id=project_id,
            job_id=reply.get("job_id"),
            spend_confirmed=body.confirm_spend,
        )
        job_id = reply.get("job_id")
        return CadLoopStarted(
            job_id=job_id,
            status=str(reply.get("status") or "running"),
            operation_id=f"job:{job_id}" if job_id else None,
            reused=bool(reply.get("reused")),
            expected_seconds=reply.get("expected_seconds"),
            spend=info,
            result=reply.get("result"),
            error=reply.get("error"),
        )


@router.get("/projects/{project_id}/cad-loops/{node_id}")
async def get_cad_loop(project_id: str, node_id: str) -> dict[str, Any]:
    """One run with every iteration (script, checks, views, critique, DFM, cost)."""
    with tracer.start_as_current_span("cad_loop_routes.get") as span:
        span.set_attribute("project_id", project_id)
        _uuid(project_id)
        service = _get_service()
        try:
            return service.view_of(await service.get(project_id, node_id))
        except CadLoopError as exc:
            span.record_exception(exc)
            raise _http(exc) from exc


@router.post("/projects/{project_id}/cad-loops/{node_id}/decision")
async def decide_cad_loop(
    project_id: str, node_id: str, body: CadLoopDecisionRequest, request: Request
) -> dict[str, Any]:
    """Use the proposed version (built through the exact path) or keep the previous one."""
    _refuse_agent(request, "a person decides whether to use a proposed CAD version")
    actor = str(approver_from_request(request).actor_id)
    with tracer.start_as_current_span("cad_loop_routes.decision") as span:
        span.set_attribute("project_id", project_id)
        span.set_attribute("decision", body.decision)
        _uuid(project_id)
        try:
            return await _get_service().decide(
                project_id,
                node_id,
                decision=body.decision,
                actor=f"person:{actor}",
                session_id=f"create-{project_id}",
            )
        except (CadLoopError, LoopUnavailableError) as exc:
            span.record_exception(exc)
            raise _http(exc) from exc


__all__ = ["TARGET_PREFIX", "router", "set_job_runner", "set_service"]
