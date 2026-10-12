"""A project becomes active when work starts on it (FORGE-606)."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest

from api_gateway.projects import routes as project_routes
from api_gateway.projects.backend import InMemoryProjectBackend
from api_gateway.runs.routes import (
    _activate_run_project,
    activate_projects_with_runs,
    get_run_store,
    reset_run_store,
)


@pytest.fixture
def backend() -> Iterator[InMemoryProjectBackend]:
    previous = project_routes.get_project_backend()
    fresh = InMemoryProjectBackend.create()
    project_routes.init_project_backend(fresh)
    reset_run_store()
    try:
        yield fresh
    finally:
        project_routes.init_project_backend(previous)
        reset_run_store()


def _project(backend: InMemoryProjectBackend, name: str, status: str = "draft") -> str:
    created = asyncio.run(backend.create_project(name=name, description="", status=status))
    return str(created.id)


def _status(backend: InMemoryProjectBackend, project_id: str) -> str:
    project = asyncio.run(backend.get_project(project_id))
    assert project is not None
    return str(project.status)


def test_starting_a_run_activates_a_draft_project(backend: InMemoryProjectBackend) -> None:
    pid = _project(backend, "Table")
    run = get_run_store().create({"project_id": pid})
    asyncio.run(_activate_run_project(run))
    assert _status(backend, pid) == "active"


def test_an_archived_project_stays_archived(backend: InMemoryProjectBackend) -> None:
    pid = _project(backend, "Old shelf", status="archived")
    run = get_run_store().create({"project_id": pid})
    asyncio.run(_activate_run_project(run))
    assert _status(backend, pid) == "archived"


def test_a_run_without_a_project_changes_nothing(backend: InMemoryProjectBackend) -> None:
    pid = _project(backend, "Untouched")
    asyncio.run(_activate_run_project(get_run_store().create({})))
    assert _status(backend, pid) == "draft"


def test_an_unknown_project_is_not_an_error(backend: InMemoryProjectBackend) -> None:
    run = get_run_store().create({"project_id": "00000000-0000-0000-0000-000000000000"})
    asyncio.run(_activate_run_project(run))  # no raise


def test_the_backfill_activates_drafts_that_already_have_runs(
    backend: InMemoryProjectBackend,
) -> None:
    worked = _project(backend, "Table")
    idle = _project(backend, "Idea only")
    store = get_run_store()
    store.create({"project_id": worked})
    store.create({"project_id": worked})  # two runs, one activation
    assert asyncio.run(activate_projects_with_runs()) == 1
    assert _status(backend, worked) == "active"
    assert _status(backend, idle) == "draft"
