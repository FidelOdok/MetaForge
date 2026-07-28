"""Project-scoped chat: the agent gets a project brief as leading history (MET-10)."""

from __future__ import annotations

from typing import Any

import pytest

from api_gateway.chat.models import ChatThreadRecord
from api_gateway.projects.schemas import ProjectResponse, ProjectWorkProductResponse


class _FakeProjectBackend:
    """Minimal project backend returning a fixed project by id."""

    def __init__(self, project: ProjectResponse | None) -> None:
        self._project = project

    async def get_project(self, project_id: str) -> ProjectResponse | None:
        if self._project and self._project.id == project_id:
            return self._project
        return None


def _thread(scope_kind: str, entity: str) -> ChatThreadRecord:
    return ChatThreadRecord(
        id="t1",
        channel_id="c1",
        scope_kind=scope_kind,
        scope_entity_id=entity,
        title="x",
    )


def _project(wps: list[ProjectWorkProductResponse]) -> ProjectResponse:
    return ProjectResponse(
        id="p-123",
        name="Pan-Tilt Gimbal",
        description="A 2-axis camera gimbal",
        status="active",
        work_products=wps,
        last_updated="2026-07-01T00:00:00Z",
        created_at="2026-07-01T00:00:00Z",
    )


def _wp(name: str, wp_type: str) -> ProjectWorkProductResponse:
    return ProjectWorkProductResponse(
        id=f"wp-{name}", name=name, type=wp_type, status="draft", updated_at="2026-07-01T00:00:00Z"
    )


async def _brief(monkeypatch: pytest.MonkeyPatch, thread: ChatThreadRecord, project: Any) -> list:
    import api_gateway.chat.routes as routes

    monkeypatch.setattr(routes, "_project_backend", _FakeProjectBackend(project))
    return await routes._project_brief(thread)


@pytest.mark.asyncio
async def test_non_project_scope_gets_no_brief(monkeypatch: pytest.MonkeyPatch) -> None:
    out = await _brief(monkeypatch, _thread("session", "s1"), _project([]))
    assert out == []


@pytest.mark.asyncio
async def test_missing_project_gets_no_brief(monkeypatch: pytest.MonkeyPatch) -> None:
    out = await _brief(monkeypatch, _thread("project", "nope"), _project([]))
    assert out == []


@pytest.mark.asyncio
async def test_project_brief_lists_work_products_and_commit_instruction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project([_wp("Gimbal Base", "cad_model"), _wp("Yaw Housing", "cad_model")])
    out = await _brief(monkeypatch, _thread("project", "p-123"), project)

    assert len(out) == 2
    assert out[0]["role"] == "user" and out[1]["role"] == "assistant"
    brief = out[0]["content"]
    # Names the project + both work products so the agent can reason over them.
    assert "Pan-Tilt Gimbal" in brief
    assert "Gimbal Base" in brief and "Yaw Housing" in brief
    # Tells the agent how to persist new work into the project.
    assert "p-123" in brief and "project_id" in brief
    assert "twin.commit_geometry" in brief


@pytest.mark.asyncio
async def test_project_brief_caps_work_product_list(monkeypatch: pytest.MonkeyPatch) -> None:
    import api_gateway.chat.routes as routes

    many = [_wp(f"Part {i}", "cad_model") for i in range(routes._PROJECT_WP_LIMIT + 5)]
    out = await _brief(monkeypatch, _thread("project", "p-123"), _project(many))
    brief = out[0]["content"]
    assert "and 5 more" in brief
