"""Project-scoped chat: the agent gets a project brief (MET-10 / MET-566).

Since MET-566 the brief is TEXT (placement — layered system prompt on the
native path, legacy history pair on ReAct — is decided in
``harness_backend._apply_turn_context``, tested separately).
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from api_gateway.chat.models import ChatThreadRecord
from api_gateway.projects.schemas import ProjectResponse, ProjectWorkProductResponse
from twin_core.models.enums import WorkProductType
from twin_core.models.work_product import WorkProduct


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


async def _brief(
    monkeypatch: pytest.MonkeyPatch, thread: ChatThreadRecord, project: Any
) -> str | None:
    # MET-575: chat resolves the projects backend through the accessor at
    # call time (the import-time alias silently pinned the empty in-memory
    # store after startup swapped in Postgres) — so tests patch the projects
    # module's backend, exactly like ``init_project_backend`` does.
    import api_gateway.chat.routes as routes
    import api_gateway.projects.routes as projects_routes

    monkeypatch.setattr(projects_routes, "_backend", _FakeProjectBackend(project))
    return await routes._project_brief(thread)


@pytest.mark.asyncio
async def test_brief_sees_backend_swapped_after_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MET-575 regression: the brief must consult the CURRENT projects
    backend, not whichever instance existed when chat.routes was imported.
    The old ``from ... import _backend as _project_backend`` alias made every
    project-scoped chat lose its brief on any deployment that swaps the
    backend at startup."""
    from api_gateway.projects.routes import init_project_backend

    project = _project([_wp("Bracket", "cad_model")])
    swapped = _FakeProjectBackend(project)
    import api_gateway.projects.routes as projects_routes

    original = projects_routes._backend
    try:
        init_project_backend(swapped)  # the real startup swap, after import
        import api_gateway.chat.routes as routes

        out = await routes._project_brief(_thread("project", "p-123"))
        assert out, "brief must be built from the swapped-in backend"
        assert "p-123" in out
    finally:
        init_project_backend(original)


@pytest.mark.asyncio
async def test_non_project_scope_gets_no_brief(monkeypatch: pytest.MonkeyPatch) -> None:
    out = await _brief(monkeypatch, _thread("session", "s1"), _project([]))
    assert out is None


@pytest.mark.asyncio
async def test_missing_project_gets_no_brief(monkeypatch: pytest.MonkeyPatch) -> None:
    out = await _brief(monkeypatch, _thread("project", "nope"), _project([]))
    assert out is None


@pytest.mark.asyncio
async def test_project_brief_lists_work_products_and_commit_instruction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project([_wp("Gimbal Base", "cad_model"), _wp("Yaw Housing", "cad_model")])
    brief = await _brief(monkeypatch, _thread("project", "p-123"), project)

    assert brief is not None
    # Names the project + both work products so the agent can reason over them.
    assert "Pan-Tilt Gimbal" in brief
    assert "Gimbal Base" in brief and "Yaw Housing" in brief
    # Tells the agent how to persist new work into the project.
    assert "p-123" in brief and "project_id" in brief
    assert "twin.commit_geometry" in brief


@pytest.mark.asyncio
async def test_project_brief_carries_constraint_violations_instruction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FORGE-75: the brief tells the agent to pass project_id explicitly on
    twin.constraint_violations, the same convention as commit_geometry --
    the ambient mcp_core.context binding FORGE-74 added is not reachable
    from a real chat turn."""
    project = _project([_wp("Gimbal Base", "cad_model")])
    brief = await _brief(monkeypatch, _thread("project", "p-123"), project)

    assert brief is not None
    assert "twin.constraint_violations" in brief
    assert brief.count("p-123") >= 2  # named on both commit_geometry and this instruction


@pytest.mark.asyncio
async def test_project_brief_carries_engineering_entity_project_id_instruction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FORGE-78: a waiver (or any EngineeringEntity) recorded via chat with
    no project_id is invisible to this project's gate checks -- e.g.
    evaluate_g8_release's waivers_approved check never saw one recorded
    this way, silently reporting a vacuous PASS. Same convention as
    FORGE-75's constraint_violations nudge."""
    project = _project([_wp("Gimbal Base", "cad_model")])
    brief = await _brief(monkeypatch, _thread("project", "p-123"), project)

    assert brief is not None
    assert "twin.record_engineering_entity" in brief
    assert brief.count("p-123") >= 3  # commit_geometry, constraint_violations, this instruction


@pytest.mark.asyncio
async def test_project_brief_caps_work_product_list(monkeypatch: pytest.MonkeyPatch) -> None:
    import api_gateway.chat.routes as routes

    many = [_wp(f"Part {i}", "cad_model") for i in range(routes._PROJECT_WP_LIMIT + 5)]
    brief = await _brief(monkeypatch, _thread("project", "p-123"), _project(many))
    assert brief is not None
    assert "and 5 more" in brief


# --------------------------------------------------------------------------
# Requirements-discovery directive (MET-584)
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bare_project_brief_carries_requirements_directive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No prd/constraint_set in the project -> the brief must tell the agent
    to elicit and record requirements before substantive design work."""
    brief = await _brief(monkeypatch, _thread("project", "p-123"), _project([]))
    assert brief is not None
    assert "NO recorded requirements" in brief
    assert "twin.record_constraint_set" in brief
    assert "Ask before you assume" in brief


@pytest.mark.asyncio
async def test_constrained_project_brief_has_no_directive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project([_wp("Bracket requirements", "constraint_set")])
    brief = await _brief(monkeypatch, _thread("project", "p-123"), project)
    assert brief is not None
    assert "NO recorded requirements" not in brief


@pytest.mark.asyncio
async def test_prd_alone_also_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    brief = await _brief(monkeypatch, _thread("project", "p-123"), _project([_wp("PRD", "prd")]))
    assert brief is not None
    assert "NO recorded requirements" not in brief


# --------------------------------------------------------------------------
# Intent/needs discovery directive (FORGE-49, epic FORGE-35)
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bare_project_brief_carries_intent_directive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No intent/stakeholder_need in the project -> the brief must tell the
    agent to elicit WHY the product exists before recording an intent."""
    brief = await _brief(monkeypatch, _thread("project", "p-123"), _project([]))
    assert brief is not None
    assert "NO recorded intent" in brief
    assert "twin.record_engineering_entity" in brief
    assert "entity_type='intent'" in brief


@pytest.mark.asyncio
async def test_intent_alone_clears_the_intent_directive(monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project([_wp("Desktop quadruped intent", "intent")])
    brief = await _brief(monkeypatch, _thread("project", "p-123"), project)
    assert brief is not None
    assert "NO recorded intent" not in brief


@pytest.mark.asyncio
async def test_stakeholder_need_alone_also_clears_the_intent_directive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project([_wp("Operator safety need", "stakeholder_need")])
    brief = await _brief(monkeypatch, _thread("project", "p-123"), project)
    assert brief is not None
    assert "NO recorded intent" not in brief


@pytest.mark.asyncio
async def test_project_with_requirements_gets_the_go_deeper_nudge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project([_wp("Bracket requirements", "constraint_set")])
    brief = await _brief(monkeypatch, _thread("project", "p-123"), project)
    assert brief is not None
    assert "parent_refs" in brief
    assert "traceable" in brief


@pytest.mark.asyncio
async def test_project_without_requirements_gets_no_go_deeper_nudge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    brief = await _brief(monkeypatch, _thread("project", "p-123"), _project([]))
    assert brief is not None
    assert "traceable" not in brief


# --------------------------------------------------------------------------
# Requirement doc content excerpts (FORGE-86)
# --------------------------------------------------------------------------


def _doc_wp(name: str, wp_type: str, wp_id: str, updated_at: str) -> ProjectWorkProductResponse:
    return ProjectWorkProductResponse(
        id=wp_id, name=name, type=wp_type, status="created", updated_at=updated_at
    )


class _FakeTwinForBrief:
    """Minimal twin stub returning a fixed WorkProduct by id."""

    def __init__(self, work_products: dict[str, WorkProduct]) -> None:
        self._wps = work_products

    async def get_work_product(self, wp_id: Any) -> WorkProduct | None:
        return self._wps.get(str(wp_id))


def _make_wp(name: str) -> WorkProduct:
    return WorkProduct(
        name=name,
        type=WorkProductType.CONSTRAINT_SET,
        domain="systems",
        file_path="",
        content_hash="sha256:test",
        format="md",
        created_by="human",
        metadata={"minio_object_key": f"work-products/{name}/doc.md"},
    )


async def _brief_with_twin(
    monkeypatch: pytest.MonkeyPatch, thread: ChatThreadRecord, project: Any, twin: Any
) -> str | None:
    import api_gateway.chat.routes as routes
    import api_gateway.projects.routes as projects_routes

    monkeypatch.setattr(projects_routes, "_backend", _FakeProjectBackend(project))
    monkeypatch.setattr(routes, "_twin", twin)
    return await routes._project_brief(thread)


@pytest.mark.asyncio
async def test_requirement_doc_excerpt_is_inlined(monkeypatch: pytest.MonkeyPatch) -> None:
    """A prd/constraint_set's actual content -- not just its name -- reaches
    the brief, resolved via the same blob mechanism every other
    work-product-content reader in this codebase already uses."""
    wp_id = str(uuid4())
    stored_wp = _make_wp("Bracket requirements")
    monkeypatch.setattr(
        "api_gateway.twin.blob_store.resolve_work_product_blob",
        lambda wp: (b"Max mass: 500g. Max cost: $10.", "doc.md"),
    )

    project = _project(
        [_doc_wp("Bracket requirements", "constraint_set", wp_id, "2026-07-01T00:00:00Z")]
    )
    twin = _FakeTwinForBrief({wp_id: stored_wp})
    brief = await _brief_with_twin(monkeypatch, _thread("project", "p-123"), project, twin)

    assert brief is not None
    assert "Max mass: 500g. Max cost: $10." in brief
    assert "### Bracket requirements (constraint_set)" in brief


@pytest.mark.asyncio
async def test_requirement_doc_excerpt_is_truncated(monkeypatch: pytest.MonkeyPatch) -> None:
    import api_gateway.chat.routes as routes

    wp_id = str(uuid4())
    long_text = "x" * (routes._BRIEF_DOC_EXCERPT_CHARS + 500)
    monkeypatch.setattr(
        "api_gateway.twin.blob_store.resolve_work_product_blob",
        lambda wp: (long_text.encode(), "doc.md"),
    )

    project = _project([_doc_wp("Big PRD", "prd", wp_id, "2026-07-01T00:00:00Z")])
    twin = _FakeTwinForBrief({wp_id: _make_wp("Big PRD")})
    brief = await _brief_with_twin(monkeypatch, _thread("project", "p-123"), project, twin)

    assert brief is not None
    assert "(truncated)" in brief
    assert "x" * (routes._BRIEF_DOC_EXCERPT_CHARS + 1) not in brief


@pytest.mark.asyncio
async def test_requirement_doc_excerpt_failure_falls_back_silently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A work product id that isn't a real UUID (or any other resolution
    failure) never breaks brief assembly -- the name-only line still shows."""
    project = _project([_wp("Legacy Requirements", "prd")])  # non-UUID id from _wp()
    brief = await _brief(monkeypatch, _thread("project", "p-123"), project)

    assert brief is not None
    assert "Legacy Requirements" in brief


@pytest.mark.asyncio
async def test_requirement_doc_excerpts_capped_and_most_recent_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import api_gateway.chat.routes as routes

    ids = [str(uuid4()) for _ in range(routes._BRIEF_DOC_LIMIT + 2)]
    wps = [
        _doc_wp(f"Doc {i}", "constraint_set", ids[i], f"2026-07-{i + 1:02d}T00:00:00Z")
        for i in range(len(ids))
    ]
    monkeypatch.setattr(
        "api_gateway.twin.blob_store.resolve_work_product_blob",
        lambda wp: (f"content of {wp.name}".encode(), "doc.md"),
    )

    project = _project(wps)
    twin = _FakeTwinForBrief({i: _make_wp(f"Doc {n}") for n, i in enumerate(ids)})
    brief = await _brief_with_twin(monkeypatch, _thread("project", "p-123"), project, twin)

    assert brief is not None
    # Most recently updated (highest index) docs win, capped at the limit.
    most_recent = [
        f"Doc {i}" for i in range(len(ids) - 1, len(ids) - 1 - routes._BRIEF_DOC_LIMIT, -1)
    ]
    for name in most_recent:
        assert f"content of {name}" in brief
    oldest = "content of Doc 0"
    assert oldest not in brief
