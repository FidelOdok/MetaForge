"""Resolve a project reference and rescope a chat thread in place (MET-580).

Two entry points share this logic so a human and the agent refuse the same
ambiguous queries the same way and produce the same observable effect:

- ``PATCH /v1/chat/threads/{id}/scope`` (``chat/routes.py``) — a human/UI action.
- ``chat.set_project_scope`` (``chat/harness_backend.py``) — the agent, called
  mid-turn when a user asks in prose to switch projects.

Deliberately has no module-level backend singleton: callers pass their
``ChatBackend`` in, so there is exactly one source of truth for which backend
is live (``chat/routes.py``'s ``_backend``), never a second one to fall out of
sync with it.
"""

from __future__ import annotations

from api_gateway.chat.backend import ChatBackend
from api_gateway.chat.models import ChatThreadRecord
from api_gateway.chat.streaming import notify_scope_changed
from api_gateway.projects.routes import get_project_backend
from api_gateway.projects.schemas import ProjectResponse


class ScopeResolutionError(ValueError):
    """A project query, or a scope change, could not be resolved/applied.

    Both failure classes — "which project did you mean" and "that scope_kind/
    thread doesn't exist" — surface the same way to both callers: an HTTP 400
    from the route, a tool-call error the model sees from the native tool.
    """


async def resolve_project(query: str) -> ProjectResponse:
    """id -> exact name (case-insensitive) -> unique substring of the name.

    Mirrors ``tui/src/lib/project.ts::resolveProject`` exactly, including the
    rule that several matches is an error, never a guess — silently picking
    one would scope work to the wrong project.
    """
    q = query.strip()
    if not q:
        raise ScopeResolutionError("a project id or name is required")

    projects = await get_project_backend().list_projects()
    if not projects:
        raise ScopeResolutionError("no projects exist on this gateway")

    by_id = next((p for p in projects if p.id == q), None)
    if by_id is not None:
        return by_id

    lower = q.lower()
    exact = [p for p in projects if p.name.lower() == lower]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise ScopeResolutionError(_ambiguous(q, exact))

    partial = [p for p in projects if lower in p.name.lower()]
    if len(partial) == 1:
        return partial[0]
    if len(partial) > 1:
        raise ScopeResolutionError(_ambiguous(q, partial))

    raise ScopeResolutionError(f'no project matches "{q}"')


def _ambiguous(query: str, matches: list[ProjectResponse]) -> str:
    shown = [f"{p.name} ({p.id[:8]})" for p in matches[:5]]
    more = f", +{len(matches) - len(shown)} more" if len(matches) > len(shown) else ""
    return (
        f'"{query}" matches {len(matches)} projects: {", ".join(shown)}{more} '
        "— be more specific or use the id"
    )


async def apply_thread_scope(
    backend: ChatBackend,
    thread_id: str,
    *,
    scope_kind: str,
    scope_entity_id: str,
    project_name: str | None = None,
) -> ChatThreadRecord:
    """Validate, persist, and broadcast a scope change on an EXISTING thread.

    Rescopes ``thread_id`` in place rather than creating a new thread — the
    conversation is preserved, and ``chat/routes.py::_project_brief`` reads
    ``scope_kind``/``scope_entity_id`` fresh every turn, so the very next turn
    starts getting the new project's brief.
    """
    channel = await backend.channel_for_scope(scope_kind)
    if channel is None:
        raise ScopeResolutionError(f"no channel for scope_kind={scope_kind!r}")

    thread = await backend.update_thread_scope(
        thread_id,
        channel_id=channel.id,
        scope_kind=scope_kind,
        scope_entity_id=scope_entity_id,
    )
    if thread is None:
        raise ScopeResolutionError(f"thread {thread_id!r} not found")

    await notify_scope_changed(
        thread_id,
        scope_kind=scope_kind,
        scope_entity_id=scope_entity_id,
        project_name=project_name,
    )
    return thread
