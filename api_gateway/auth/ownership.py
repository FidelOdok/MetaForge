"""Which project an entity belongs to (FORGE-872).

On a gateway shared by several accounts (FORGE-849), a route that names a
project is checked against the caller's scope. Most routes the dashboard uses
inside a project name an entity instead: a twin node, a run, an approval, a
chat thread, an agent session, an upload. Each lookup here answers "which
project owns this id?", and :mod:`api_gateway.auth.scope` applies the same
rule to that answer as to a project in the path: outside the caller's scope
is a 404.

A lookup returns ``None`` when the entity does not exist **or** belongs to no
project. Both are refused on a shared gateway: an entity with no project
cannot be shown to belong to the caller. Lookups only run in project-scoped
mode; a local gateway loads none of this.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.auth.ownership")

#: ``(app, entity_id) -> owning project id, or None``.
OwnerLookup = Callable[[Any, str], Awaitable[str | None]]


def _str(value: object) -> str | None:
    return str(value) if value else None


async def twin_node_project(app: Any, node_id: str) -> str | None:
    """A work product or engineering entity in the twin."""
    from api_gateway.twin import routes as twin_routes

    try:
        uid = UUID(node_id)
    except ValueError:
        return None
    twin = twin_routes._twin  # noqa: SLF001  (rebound at startup; read it late)
    node = await twin.get_work_product(uid)
    if node is None:
        get_entity = getattr(twin, "get_engineering_entity", None)
        node = await get_entity(uid) if get_entity else None
    return _str(getattr(node, "project_id", None)) if node is not None else None


async def run_project(app: Any, run_id: str) -> str | None:
    """A run, a held tool call or a gate: all live in the run store."""
    from api_gateway.runs import routes as run_routes
    from api_gateway.runs.schemas import project_of
    from orchestrator.harness.runs import RunNotFoundError

    try:
        run = run_routes.get_run_store().get(run_id)
    except RunNotFoundError:
        return None
    return project_of(run.request)


async def approval_project(app: Any, approval_id: str) -> str | None:
    from fastapi import HTTPException

    from api_gateway.approvals import service

    try:
        item = await service.get_item(app, approval_id)
    except HTTPException:
        return None
    return _str(item.project_id)


async def chat_scope_project(app: Any, scope_kind: str, entity_id: str) -> str | None:
    """The project a chat scope belongs to. Only scopes with an owner resolve."""
    if scope_kind == "project":
        return entity_id or None
    if scope_kind == "digital-twin-node":
        return await OWNER_LOOKUPS["twin_node"](app, entity_id)
    return None


async def chat_thread_project(app: Any, thread_id: str) -> str | None:
    from api_gateway.chat import routes as chat_routes

    thread = await chat_routes._backend.get_thread(thread_id)  # noqa: SLF001
    if thread is None:
        return None
    return await chat_scope_project(app, thread.scope_kind, thread.scope_entity_id)


async def agent_session_project(app: Any, session_id: str) -> str | None:
    store = getattr(getattr(app, "state", None), "agent_session_store", None)
    if store is None:
        return None
    session = await store.get_session(session_id)
    return _str(getattr(session, "project_id", None)) if session is not None else None


async def upload_project(app: Any, upload_id: str) -> str | None:
    from api_gateway.uploads import routes as upload_routes
    from api_gateway.uploads.store import UploadNotFoundError

    try:
        upload = upload_routes.get_upload_store().get(upload_id)
    except UploadNotFoundError:
        return None
    return _str(upload.project_id)


#: Lookups by kind. Tests replace entries; nothing else should.
OWNER_LOOKUPS: dict[str, OwnerLookup] = {
    "twin_node": twin_node_project,
    "run": run_project,
    "approval": approval_project,
    "chat_thread": chat_thread_project,
    "agent_session": agent_session_project,
    "upload": upload_project,
}


async def owner_of(app: Any, kind: str, entity_id: str) -> str | None:
    """The project owning ``entity_id`` of ``kind``, or ``None``."""
    with tracer.start_as_current_span("auth.entity_owner") as span:
        span.set_attribute("entity.kind", kind)
        project = await OWNER_LOOKUPS[kind](app, entity_id)
        span.set_attribute("entity.resolved", project is not None)
        return project
