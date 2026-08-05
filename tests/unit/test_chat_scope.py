"""Project resolution + in-place thread rescoping (MET-580).

``resolve_project`` mirrors ``tui/src/lib/project.ts::resolveProject`` — same
match order (id -> exact name -> unique substring), same "ambiguity is an
error, never a guess" rule — so a human's ``/project`` and the agent's
``chat.set_project_scope`` refuse the same queries the same way.
``apply_thread_scope`` is the shared persistence path both go through.
"""

from __future__ import annotations

import pytest

from api_gateway.chat.backend import InMemoryChatBackend
from api_gateway.chat.scope import ScopeResolutionError, apply_thread_scope, resolve_project
from api_gateway.projects.schemas import ProjectResponse


class _FakeProjectBackend:
    """Minimal project backend: fixed project list, lookup by id."""

    def __init__(self, projects: list[ProjectResponse]) -> None:
        self._projects = projects

    async def list_projects(self) -> list[ProjectResponse]:
        return list(self._projects)

    async def get_project(self, project_id: str) -> ProjectResponse | None:
        return next((p for p in self._projects if p.id == project_id), None)


def _project(id_: str, name: str) -> ProjectResponse:
    return ProjectResponse(
        id=id_,
        name=name,
        description="",
        status="active",
        work_products=[],
        last_updated="2026-07-01T00:00:00Z",
        created_at="2026-07-01T00:00:00Z",
    )


PROJECTS = [
    _project("cfcd6ae8-5b16-4ba1-84d7-b9384ea1cbf3", "Monitor Build Demo"),
    _project("250aec91-6d31-4a26-bb71-5e0d1e6fedb9", "Pan-Tilt Gimbal"),
    _project("aaa11111-0000-0000-0000-000000000000", "eval-chat_brief_project-native-1"),
]


@pytest.fixture(autouse=True)
def _wire_project_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    import api_gateway.projects.routes as projects_routes

    monkeypatch.setattr(projects_routes, "_backend", _FakeProjectBackend(PROJECTS))


# ---------------------------------------------------------------------------
# resolve_project
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolves_exact_id() -> None:
    p = await resolve_project("250aec91-6d31-4a26-bb71-5e0d1e6fedb9")
    assert p.name == "Pan-Tilt Gimbal"


@pytest.mark.asyncio
async def test_resolves_exact_name_case_insensitive() -> None:
    p = await resolve_project("monitor build demo")
    assert p.id == "cfcd6ae8-5b16-4ba1-84d7-b9384ea1cbf3"


@pytest.mark.asyncio
async def test_resolves_unique_substring() -> None:
    p = await resolve_project("gimbal")
    assert p.id == "250aec91-6d31-4a26-bb71-5e0d1e6fedb9"


@pytest.mark.asyncio
async def test_ambiguous_substring_is_an_error_not_a_guess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import api_gateway.projects.routes as projects_routes

    projects = [*PROJECTS, _project("bbb22222-0000-0000-0000-000000000000", "Monitor Build v2")]
    monkeypatch.setattr(projects_routes, "_backend", _FakeProjectBackend(projects))

    with pytest.raises(ScopeResolutionError) as exc:
        await resolve_project("monitor")
    assert "matches 2 projects" in str(exc.value)
    assert "Monitor Build Demo" in str(exc.value)
    assert "Monitor Build v2" in str(exc.value)


@pytest.mark.asyncio
async def test_exact_name_wins_over_other_substring_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import api_gateway.projects.routes as projects_routes

    projects = [
        *PROJECTS,
        _project("bbb22222-0000-0000-0000-000000000000", "Monitor Build Demo v2"),
    ]
    monkeypatch.setattr(projects_routes, "_backend", _FakeProjectBackend(projects))

    p = await resolve_project("Monitor Build Demo")
    assert p.id == "cfcd6ae8-5b16-4ba1-84d7-b9384ea1cbf3"


@pytest.mark.asyncio
async def test_unknown_name_reports_no_match() -> None:
    with pytest.raises(ScopeResolutionError, match='no project matches "nope"'):
        await resolve_project("nope")


@pytest.mark.asyncio
async def test_empty_query_and_no_projects_both_explain_themselves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ScopeResolutionError, match="required"):
        await resolve_project("   ")

    import api_gateway.projects.routes as projects_routes

    monkeypatch.setattr(projects_routes, "_backend", _FakeProjectBackend([]))
    with pytest.raises(ScopeResolutionError, match="no projects exist"):
        await resolve_project("anything")


# ---------------------------------------------------------------------------
# apply_thread_scope
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_thread_scope_persists_and_returns_updated_thread() -> None:
    backend = InMemoryChatBackend.create()
    channel = await backend.channel_for_scope("assistant")
    assert channel is not None
    thread = await backend.create_thread(
        channel_id=channel.id, scope_kind="assistant", scope_entity_id="e1", title="t"
    )

    updated = await apply_thread_scope(
        backend,
        thread.id,
        scope_kind="project",
        scope_entity_id="cfcd6ae8-5b16-4ba1-84d7-b9384ea1cbf3",
        project_name="Monitor Build Demo",
    )

    assert updated.id == thread.id
    assert updated.scope_kind == "project"
    assert updated.scope_entity_id == "cfcd6ae8-5b16-4ba1-84d7-b9384ea1cbf3"
    # The thread's channel must move with it — a project-scoped thread must
    # never keep pointing at the "Design Assistant" channel.
    project_channel = await backend.channel_for_scope("project")
    assert updated.channel_id == project_channel.id

    # The SAME thread continues — a fresh read confirms nothing was recreated.
    reread = await backend.get_thread(thread.id)
    assert reread is not None
    assert reread.scope_kind == "project"


@pytest.mark.asyncio
async def test_apply_thread_scope_rejects_unknown_scope_kind() -> None:
    backend = InMemoryChatBackend.create()
    channel = await backend.channel_for_scope("assistant")
    thread = await backend.create_thread(
        channel_id=channel.id, scope_kind="assistant", scope_entity_id="e1", title="t"
    )

    with pytest.raises(ScopeResolutionError, match="no channel for scope_kind"):
        await apply_thread_scope(backend, thread.id, scope_kind="nonexistent", scope_entity_id="x")

    # Refused, so the thread must be untouched.
    unchanged = await backend.get_thread(thread.id)
    assert unchanged.scope_kind == "assistant"


@pytest.mark.asyncio
async def test_apply_thread_scope_rejects_missing_thread() -> None:
    backend = InMemoryChatBackend.create()
    with pytest.raises(ScopeResolutionError, match="not found"):
        await apply_thread_scope(
            backend, "does-not-exist", scope_kind="project", scope_entity_id="p1"
        )
