"""Open or create a project from what the user said (FORGE-335).

"new project: 6-DOF arm, 1 kg payload" and "open the arm project" both have
to work from a harness. Two gaps:

* **Resolution existed three times and not on MCP.** `api_gateway/chat/scope.py`,
  `tui/src/lib/project.ts` and the CLI each turned a name into a project; an
  MCP client could only pass a UUID, so a harness asked to open "the arm
  project" had to list everything and match it itself — which is how you get
  one that quietly picks the first hit.
* **Nothing scoped the session.** `session.start` could bind, but only once
  you already had the id.

The rule these tests mostly exist to hold: several matches is an error that
names them, never a guess.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest

from mcp_core.context import (
    HEADER_SESSION,
    bound_project,
    context_from_headers,
    reset_session_projects,
    with_context,
)
from mcp_core.project_ref import (
    AmbiguousProjectRef,
    NoProjectsExist,
    ProjectRef,
    ProjectRefError,
    UnknownProjectRef,
    resolve_project_ref,
)


@pytest.fixture(autouse=True)
def _clean():
    reset_session_projects()
    yield
    reset_session_projects()


# ---------------------------------------------------------------------------
# The matcher itself
# ---------------------------------------------------------------------------

ARM = ProjectRef(id="11111111-1111-1111-1111-111111111111", name="6-DOF Arm")
ARM2 = ProjectRef(id="22222222-2222-2222-2222-222222222222", name="Arm Mk2")
GIMBAL = ProjectRef(id="33333333-3333-3333-3333-333333333333", name="Gimbal")
ALL = [ARM, ARM2, GIMBAL]


def test_id_wins() -> None:
    assert resolve_project_ref(ARM.id, ALL) is ARM


def test_exact_name_is_case_insensitive() -> None:
    assert resolve_project_ref("6-dof arm", ALL) is ARM


def test_unique_substring() -> None:
    assert resolve_project_ref("gim", ALL) is GIMBAL


def test_exact_name_beats_a_substring_that_also_matches() -> None:
    """Otherwise naming a project exactly becomes ambiguous the moment a
    longer name contains it."""
    short = ProjectRef(id="44444444-4444-4444-4444-444444444444", name="Arm")
    assert resolve_project_ref("Arm", [short, ARM, ARM2]) is short


def test_several_matches_is_an_error_naming_them() -> None:
    with pytest.raises(AmbiguousProjectRef) as caught:
        resolve_project_ref("arm", ALL)
    assert "6-DOF Arm" in str(caught.value)
    assert "Arm Mk2" in str(caught.value)
    assert caught.value.matches == [ARM, ARM2]


def test_a_long_ambiguous_list_is_summarised() -> None:
    many = [ProjectRef(id=str(uuid.uuid4()), name=f"Arm {i}") for i in range(9)]
    with pytest.raises(AmbiguousProjectRef) as caught:
        resolve_project_ref("arm", many)
    assert "+4 more" in str(caught.value)


def test_no_match_and_no_projects_are_different_problems() -> None:
    with pytest.raises(UnknownProjectRef):
        resolve_project_ref("submarine", ALL)
    with pytest.raises(NoProjectsExist):
        resolve_project_ref("anything", [])


def test_an_empty_query_is_refused() -> None:
    with pytest.raises(ProjectRefError):
        resolve_project_ref("   ", ALL)


def test_the_gateway_uses_this_matcher_rather_than_its_own() -> None:
    """The extraction, asserted. A second copy is a copy that can disagree,
    and the disagreement nobody notices scopes work to the wrong project."""
    import inspect

    from api_gateway.chat import scope

    source = inspect.getsource(scope.resolve_project)
    assert "resolve_project_ref" in source
    assert "lower in p.name.lower()" not in source, "matching was reimplemented here"


# ---------------------------------------------------------------------------
# project.open over MCP
# ---------------------------------------------------------------------------


@dataclass
class _Project:
    id: str
    name: str
    description: str = ""
    status: str = "draft"
    agent_count: int = 0
    created_at: str = "2026-01-01T00:00:00Z"
    last_updated: str = "2026-01-01T00:00:00Z"
    work_products: list[Any] = field(default_factory=list)


class _Backend:
    def __init__(self, projects: list[_Project]) -> None:
        self._projects = projects
        self.created: list[str] = []

    async def list_projects(self) -> list[_Project]:
        return list(self._projects)

    async def get_project(self, project_id: str) -> _Project | None:
        return next((p for p in self._projects if p.id == project_id), None)

    async def create_project(self, *, name: str, description: str, status: str) -> _Project:
        made = _Project(id=str(uuid.uuid4()), name=name, description=description, status=status)
        self._projects.append(made)
        self.created.append(name)
        return made

    async def update_project(self, project_id: str, **kw: Any) -> _Project | None:
        return await self.get_project(project_id)

    async def delete_project(self, project_id: str) -> bool:
        return True


def _server(projects: list[_Project] | None = None):
    from tool_registry.tools.project.adapter import ProjectServer

    server = ProjectServer()
    server.set_backend(_Backend(projects if projects is not None else []))
    return server


def _projects() -> list[_Project]:
    return [_Project(id=p.id, name=p.name) for p in ALL]


def test_open_resolves_a_prose_reference() -> None:
    server = _server(_projects())
    session = uuid.uuid4()
    with with_context(context_from_headers({HEADER_SESSION: str(session)})):
        out = asyncio.run(server.handle_open({"query": "gim"}))
    assert out["project"]["name"] == "Gimbal"


def test_open_scopes_the_session() -> None:
    """The point of the tool: the next call does not have to say which
    project, and resolves to this one."""
    server = _server(_projects())
    session = uuid.uuid4()
    with with_context(context_from_headers({HEADER_SESSION: str(session)})):
        out = asyncio.run(server.handle_open({"query": "Gimbal"}))
    assert out["scope_bound"] is True
    assert str(bound_project(session)) == GIMBAL.id


def test_open_reports_honestly_when_the_scope_cannot_stick() -> None:
    server = _server(_projects())
    with with_context(context_from_headers({})):
        out = asyncio.run(server.handle_open({"query": "Gimbal"}))
    assert out["scope_bound"] is False
    assert out["project"]["name"] == "Gimbal"  # the lookup still worked


def test_open_refuses_an_ambiguous_reference() -> None:
    server = _server(_projects())
    with pytest.raises(ValueError, match="matches 2 projects"):
        asyncio.run(server.handle_open({"query": "arm"}))


def test_open_refuses_an_unknown_reference_rather_than_creating_one() -> None:
    """An empty result would invite the model to create a duplicate."""
    server = _server(_projects())
    with pytest.raises(ValueError, match="no project matches"):
        asyncio.run(server.handle_open({"query": "submarine"}))


def test_create_scopes_the_session_too() -> None:
    """Creating a project and then not being in it is the same papercut."""
    server = _server([])
    session = uuid.uuid4()
    with with_context(context_from_headers({HEADER_SESSION: str(session)})):
        out = asyncio.run(
            server.handle_create({"name": "6-DOF Arm", "description": "1 kg payload"})
        )
    assert out["scope_bound"] is True
    assert str(bound_project(session)) == out["id"]


# ---------------------------------------------------------------------------
# The reviewer has to be told where a write lands
# ---------------------------------------------------------------------------


def test_the_approval_prompt_names_the_effective_project() -> None:
    """Once a project is set with project.open, later calls carry no project
    of their own — so without this a reviewer approving a commit is told
    everything except which project it writes to."""
    from mcp_core.elicitation import approval_request
    from mcp_core.guardrails import ApprovalAsk, Caller

    message, _ = approval_request(
        ApprovalAsk(
            tool_id="twin.commit_geometry",
            arguments={"obj_id": "bracket"},
            caller=Caller.REMOTE,
            reason="writes; held for approval (remote caller)",
            project="6-DOF Arm",
        )
    )
    assert "Project: 6-DOF Arm" in message


def test_an_explicit_project_argument_is_what_is_shown() -> None:
    from metaforge.mcp.server import _effective_project

    assert _effective_project({"project_id": "abc"}) == "abc"


def test_the_session_scope_is_shown_when_the_call_names_none() -> None:
    from metaforge.mcp.server import _effective_project

    project = uuid.uuid4()
    with with_context(context_from_headers({"X-MetaForge-Project": str(project)})):
        assert _effective_project({}) == str(project)


# ---------------------------------------------------------------------------
# Being briefed, rather than being offered a brief (FORGE-337)
# ---------------------------------------------------------------------------


def test_open_returns_the_brief_inline() -> None:
    """`/metaforge:use` told the agent to read the brief resource, so being
    briefed depended on the client supporting resources *and* the agent
    choosing to follow the instruction. An agent that skipped it looked
    exactly like one that had opened an empty project."""
    from tool_registry.tools.project.adapter import ProjectServer

    async def provider(kind: str, project_id: str) -> str:
        assert kind == "brief"
        return f"# {project_id}\n\nNewest work first."

    server = ProjectServer(brief_provider=provider)
    server.set_backend(_Backend(_projects()))
    out = asyncio.run(server.handle_open({"query": "Gimbal"}))
    assert "Newest work first." in out["brief"]


def test_a_deployment_without_a_brief_provider_still_opens() -> None:
    """No brief is a missing key, not a failed open — and not an empty
    string, which would read as a project with nothing in it."""
    server = _server(_projects())
    out = asyncio.run(server.handle_open({"query": "Gimbal"}))
    assert "brief" not in out
    assert out["project"]["name"] == "Gimbal"


def test_a_failing_brief_does_not_fail_the_open() -> None:
    from tool_registry.tools.project.adapter import ProjectServer

    async def provider(kind: str, project_id: str) -> str:
        raise RuntimeError("twin unreachable")

    server = ProjectServer(brief_provider=provider)
    server.set_backend(_Backend(_projects()))
    out = asyncio.run(server.handle_open({"query": "Gimbal"}))
    assert "brief" not in out
    assert out["project"]["name"] == "Gimbal"


def test_a_blank_brief_is_treated_as_none() -> None:
    from tool_registry.tools.project.adapter import ProjectServer

    async def provider(kind: str, project_id: str) -> str:
        return "   "

    server = ProjectServer(brief_provider=provider)
    server.set_backend(_Backend(_projects()))
    assert "brief" not in asyncio.run(server.handle_open({"query": "Gimbal"}))


def test_the_sidecar_bootstrap_passes_a_brief_provider() -> None:
    """The ratchet. The sidecar served *no* resources because
    build_unified_server never forwarded one, and registration is
    conditional on the provider — so nothing failed, the resources were
    simply never there."""
    import inspect

    from metaforge.mcp import server as unified

    assert "brief_provider" in inspect.signature(unified.build_unified_server).parameters
    entry = inspect.getsource(
        __import__("metaforge.mcp.__main__", fromlist=["_bootstrap"])._bootstrap
    )
    assert "make_brief_provider" in entry
    assert "brief_provider=brief_provider" in entry
