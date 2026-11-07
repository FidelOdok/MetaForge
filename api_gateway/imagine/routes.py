"""Imagine stage routes, under ``/v1/twin/projects/{project_id}/imagine``.

- ``GET``/``PUT /studio``: the studio (status, revision, config, the browser
  collections) and a new config revision from an agent.
- ``GET /engine``: the formgraph engine page with the project's config
  embedded, under a strict Content Security Policy. ``GET /engine/assets/{name}``
  serves the engine's own scripts and stylesheet (three.js r128 vendored).
- ``POST /events``: one browser write, idempotent per ``event_id``, mapped
  into the twin.
- ``GET``/``POST /handover``: the checklist, and the gate to Develop (409 with
  the missing items when it is not ready).
- ``POST /rework``: reopen Imagine on a new revision; records made for the
  handed-over revision go stale.

Authentication is the gateway's middleware, like every other route. The
handover and the rework are a person's decisions, refused to an agent
surface (``X-MetaForge-Surface: agent``). The logic is in
:mod:`api_gateway.imagine.service`; the contract is ``docs/imagine-stage.md``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, TypeVar
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from api_gateway.imagine import engine_page, engine_token
from api_gateway.imagine.schemas import (
    EventAccepted,
    HandoverChecklist,
    HandoverPost,
    HandoverResult,
    ImagineEvent,
    ReworkPost,
    StudioPut,
    StudioResponse,
)
from api_gateway.imagine.service import HandoverNotReady, ImagineError, ImagineService
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.imagine.routes")

router = APIRouter(prefix="/v1/twin", tags=["imagine"])

_BASE = "/projects/{project_id}/imagine"
SURFACE_HEADER = "X-MetaForge-Surface"

T = TypeVar("T")

_service: ImagineService | None = None


def get_service() -> ImagineService:
    """The process's Imagine service over the gateway's twin."""
    global _service  # noqa: PLW0603
    from api_gateway.twin.routes import get_twin

    twin = get_twin()
    if _service is None or _service.twin is not twin:
        backend = None
        try:
            from api_gateway.projects.routes import get_project_backend

            backend = get_project_backend()
        except Exception:  # noqa: BLE001 - linking to the project is best-effort
            backend = None
        _service = ImagineService(twin, backend)
    return _service


def set_service(service: ImagineService | None) -> None:
    """Install a service (tests, the MCP sidecar)."""
    global _service  # noqa: PLW0603
    _service = service


def _actor(request: Request) -> str:
    from api_gateway.auth.approver import approver_from_request

    return str(approver_from_request(request).actor_id)


def _is_agent(request: Request) -> bool:
    return (request.headers.get(SURFACE_HEADER) or "").strip().lower() == "agent"


def _require_person(request: Request, what: str) -> None:
    if _is_agent(request):
        raise HTTPException(
            status_code=403, detail=f"{what} is a person's decision, not an agent's"
        )


def _person_or_delegate(request: Request, what: str) -> tuple[str, str | None, str | None]:
    """(actor, agent, on_behalf_of) for a person's step (FORGE-824).

    A person's call is theirs. The agent surface is refused (403) unless the
    gateway owner delegates human-authority decisions
    (``METAFORGE_ALLOW_AGENT_HUMAN_AUTHORITY``, docs/approvals.md); then the
    call must name the agent and the person it acts for (422 otherwise, as
    the approvals API), and is recorded as the agent's, for that person.
    """
    if not _is_agent(request):
        return _actor(request), None, None
    from api_gateway.approvals.routes import (
        AGENT_HEADER,
        ON_BEHALF_OF_HEADER,
        resolve_agent_identity,
    )
    from api_gateway.approvals.service import agent_may_decide_human_authority
    from api_gateway.auth.approver import approver_from_request

    if not agent_may_decide_human_authority():
        _require_person(request, what)
    agent = (request.headers.get(AGENT_HEADER) or "").strip()[:100]
    if not agent:
        raise HTTPException(
            status_code=422,
            detail=f"{AGENT_HEADER} is required with {SURFACE_HEADER}: agent",
        )
    on_behalf = (request.headers.get(ON_BEHALF_OF_HEADER) or "").strip() or None
    person = resolve_agent_identity(approver_from_request(request), on_behalf)
    logger.info("imagine_agent_delegated", what=what, agent=agent, on_behalf_of=person.actor_id)
    return f"agent:{agent}", agent, str(person.actor_id)


async def _call(name: str, project_id: str, fn: Callable[[], Awaitable[T]]) -> T:
    """Run one call, mapping refusals to their status and anything else to 502."""
    from twin_core.items import ItemError

    try:
        UUID(project_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid project id format") from None
    with tracer.start_as_current_span(f"imagine.routes.{name}") as span:
        span.set_attribute("project_id", project_id)
        try:
            return await fn()
        except HandoverNotReady as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": exc.message,
                    "items": [i.model_dump() for i in exc.items],
                },
            ) from exc
        except ImagineError as exc:
            span.record_exception(exc)
            detail: Any = (
                exc.message if exc.detail is None else {"message": exc.message, **exc.detail}
            )
            raise HTTPException(status_code=exc.status, detail=detail) from exc
        except ItemError as exc:
            span.record_exception(exc)
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except HTTPException:
            raise
        except Exception as exc:
            span.record_exception(exc)
            logger.warning(
                "imagine_route_failed", route=name, project_id=project_id, error=str(exc)
            )
            raise HTTPException(
                status_code=502, detail="The Imagine studio is unavailable"
            ) from exc


@router.get(f"{_BASE}/studio", response_model=StudioResponse)
async def get_studio(project_id: str) -> StudioResponse:
    return await _call("get_studio", project_id, lambda: get_service().get_studio(project_id))


@router.put(f"{_BASE}/studio", response_model=StudioResponse)
async def put_studio(project_id: str, body: StudioPut, request: Request) -> StudioResponse:
    actor = _actor(request)
    return await _call(
        "put_studio",
        project_id,
        lambda: get_service().put_studio(project_id, body.config, actor=actor),
    )


@router.get(
    f"{_BASE}/engine",
    response_class=HTMLResponse,
    responses={200: {"content": {"text/html": {}}, "description": "The engine page"}},
)
async def get_engine(project_id: str, t: str | None = None) -> HTMLResponse:
    # The auth middleware lets this route through: an iframe cannot send a
    # bearer, so with authentication on it carries the signed ``t`` that
    # GET /studio put in engine_url (see engine_token).
    if engine_token.auth_required():
        try:
            engine_token.verify(t or "", project_id)
        except engine_token.EngineTokenError as exc:
            logger.info("imagine_engine_token_refused", project_id=project_id, reason=str(exc))
            raise HTTPException(status_code=401, detail=str(exc)) from exc
    page = await _call("engine", project_id, lambda: get_service().engine_html(project_id))
    return HTMLResponse(page, headers=engine_page.page_headers())


@router.get(
    f"{_BASE}/engine/assets/{{name}}",
    response_class=Response,
    responses={200: {"content": {"text/javascript": {}, "text/css": {}}}},
)
async def get_engine_asset(project_id: str, name: str) -> Response:
    if name not in engine_page.ASSETS:
        raise HTTPException(status_code=404, detail="No such engine asset")
    return Response(
        engine_page.asset_bytes(name),
        media_type=engine_page.asset_media_type(name),
        headers=engine_page.asset_headers(),
    )


@router.post(f"{_BASE}/events", response_model=EventAccepted)
async def post_event(project_id: str, body: ImagineEvent, request: Request) -> EventAccepted:
    """One studio write. A person's acceptance, from an agent, only under delegation.

    Confirming an assumption or a scenario is the person's statement. From
    the agent surface it is refused (403) unless the owner delegates
    human-authority decisions; then it needs ``X-MetaForge-Agent`` and
    ``X-MetaForge-On-Behalf-Of`` (422 without) and is recorded as the
    agent's acceptance for that person (FORGE-827). An assumption so
    confirmed is recorded as accepted: it stays an assumption.
    """
    inner = body.data.get("data") if isinstance(body.data.get("data"), dict) else body.data
    confirms = (body.kind == "assumption_confirmed" and inner.get("confirmed") is True) or (
        body.kind == "scenario" and inner.get("confirmed") is True
    )
    delegated: dict[str, Any] | None = None
    if confirms and _is_agent(request):
        what = "Confirming an assumption" if body.kind != "scenario" else "Confirming a scenario"
        actor, agent, on_behalf_of = _person_or_delegate(request, what)
        delegated = {"by": actor, "agent": agent, "on_behalf_of": on_behalf_of}
    else:
        actor = _actor(request)
    from_agent = _is_agent(request)
    return await _call(
        "event",
        project_id,
        lambda: get_service().post_event(
            project_id, body, actor=actor, delegated=delegated, from_agent=from_agent
        ),
    )


@router.get(f"{_BASE}/handover", response_model=HandoverChecklist)
async def get_handover(project_id: str) -> HandoverChecklist:
    return await _call("handover_status", project_id, lambda: get_service().checklist(project_id))


@router.post(
    f"{_BASE}/handover",
    response_model=HandoverResult,
    responses={409: {"description": "Not ready: the missing checklist items"}},
)
async def post_handover(project_id: str, body: HandoverPost, request: Request) -> HandoverResult:
    actor, agent, on_behalf_of = _person_or_delegate(request, "Handing Imagine over to Develop")
    return await _call(
        "handover",
        project_id,
        lambda: get_service().handover(
            project_id, body.op_id, actor=actor, agent=agent, on_behalf_of=on_behalf_of
        ),
    )


@router.post(f"{_BASE}/rework", response_model=StudioResponse)
async def post_rework(project_id: str, body: ReworkPost, request: Request) -> StudioResponse:
    actor, agent, on_behalf_of = _person_or_delegate(request, "Reworking Imagine")
    return await _call(
        "rework",
        project_id,
        lambda: get_service().rework(
            project_id,
            body.op_id,
            body.reason,
            actor=actor,
            agent=agent,
            on_behalf_of=on_behalf_of,
        ),
    )


__all__ = ["get_service", "router", "set_service"]
