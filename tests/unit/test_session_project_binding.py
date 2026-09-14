"""session.start actually scopes later calls to its project (MET-680).

CLAUDE.md documents two equivalent ways to "set your active project":
`metaforge-capture use <id>` or `session.start(project_id=...)`. Only the first
worked — `session.start` forwarded `project_id` into the agent-session capture
store for attribution and never touched the MCP call context, so every later
`knowledge.*` / `twin.*` call still resolved to whatever the transport stamped
per call, usually the default tenant.

The binding is keyed on the call context's ``session_id``, never global. That
is the load-bearing design decision: the HTTP sidecar serves several clients
at once, so a process-wide "last project started" would leak one client's
project into another's calls — a worse bug than the one being fixed. The
cross-client test below is the one that matters most.
"""

from __future__ import annotations

import uuid

import pytest

from mcp_core.context import (
    HEADER_PROJECT,
    HEADER_SESSION,
    bind_session_project,
    bound_project,
    clear_session_project,
    context_from_env,
    context_from_headers,
    reset_session_projects,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_session_projects()
    yield
    reset_session_projects()


class TestBindingResolution:
    def test_a_bound_session_scopes_later_calls(self):
        session, project = uuid.uuid4(), uuid.uuid4()
        bind_session_project(session, project)

        ctx = context_from_headers({HEADER_SESSION: str(session)})

        assert ctx.project_id == project

    def test_an_explicit_per_call_project_wins_over_the_binding(self):
        # A caller naming a project on the call is being more specific than a
        # binding made earlier, so the binding must not override it.
        session, bound, explicit = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        bind_session_project(session, bound)

        ctx = context_from_headers({HEADER_SESSION: str(session), HEADER_PROJECT: str(explicit)})

        assert ctx.project_id == explicit

    def test_a_client_with_no_session_identity_gets_no_binding(self):
        # THE safety property. Without a session header the context generates a
        # fresh session_id per call, so it can never match a stored key --
        # meaning an unidentified client cannot inherit somebody else's scope.
        bind_session_project(uuid.uuid4(), uuid.uuid4())

        ctx = context_from_headers({})

        assert ctx.project_id is None

    def test_one_clients_binding_never_reaches_another(self):
        # The reason this is keyed on session rather than being global.
        session_a, project_a = uuid.uuid4(), uuid.uuid4()
        session_b = uuid.uuid4()
        bind_session_project(session_a, project_a)

        ctx_a = context_from_headers({HEADER_SESSION: str(session_a)})
        ctx_b = context_from_headers({HEADER_SESSION: str(session_b)})

        assert ctx_a.project_id == project_a
        assert ctx_b.project_id is None

    def test_stdio_env_transport_is_bound_too(self):
        # stdio is where this works best: one process, one session id.
        session, project = uuid.uuid4(), uuid.uuid4()
        bind_session_project(session, project)

        ctx = context_from_env({"METAFORGE_SESSION_ID": str(session)})

        assert ctx.project_id == project

    def test_clearing_a_binding_stops_the_scoping(self):
        session, project = uuid.uuid4(), uuid.uuid4()
        bind_session_project(session, project)
        clear_session_project(session)

        assert context_from_headers({HEADER_SESSION: str(session)}).project_id is None

    def test_rebinding_replaces_rather_than_stacks(self):
        session, first, second = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        bind_session_project(session, first)
        bind_session_project(session, second)

        assert bound_project(session) == second

    def test_the_registry_is_bounded(self):
        # A long-lived sidecar must not grow this forever. An evicted session
        # degrades to per-call project_id, never to a *wrong* project.
        from mcp_core.context import _MAX_SESSION_BINDINGS

        first = uuid.uuid4()
        bind_session_project(first, uuid.uuid4())
        for _ in range(_MAX_SESSION_BINDINGS):
            bind_session_project(uuid.uuid4(), uuid.uuid4())

        assert bound_project(first) is None


class TestSessionStartWiring:
    """``session.start`` reports honestly whether the scope took effect."""

    @staticmethod
    def _server():
        from api_gateway.sessions.backend import InMemoryAgentSessionStore
        from tool_registry.tools.session.adapter import SessionServer

        return SessionServer(store=InMemoryAgentSessionStore())

    @pytest.mark.asyncio
    async def test_start_binds_and_says_so(self):
        from mcp_core.context import McpCallContext, with_context

        project = uuid.uuid4()
        session = uuid.uuid4()
        server = self._server()

        with with_context(McpCallContext(session_id=session)):
            out = await server.handle_start(
                {"agent_code": "claude-code", "task_type": "cad", "project_id": str(project)}
            )

        assert out["project_scope_bound"] is True
        assert bound_project(session) == project

    @pytest.mark.asyncio
    async def test_start_without_a_project_binds_nothing(self):
        from mcp_core.context import McpCallContext, with_context

        session = uuid.uuid4()
        server = self._server()

        with with_context(McpCallContext(session_id=session)):
            out = await server.handle_start({"agent_code": "cc", "task_type": "cad"})

        assert out["project_scope_bound"] is False
        assert bound_project(session) is None

    @pytest.mark.asyncio
    async def test_a_malformed_project_id_reports_failure_not_a_crash(self):
        # session.start must keep working even if the scope can't be applied --
        # capture is more important than scoping.
        from mcp_core.context import McpCallContext, with_context

        session = uuid.uuid4()
        server = self._server()

        with with_context(McpCallContext(session_id=session)):
            out = await server.handle_start(
                {"agent_code": "cc", "task_type": "cad", "project_id": "not-a-uuid"}
            )

        assert out["session_id"]
        assert out["project_scope_bound"] is False

    @pytest.mark.asyncio
    async def test_completing_a_session_releases_the_scope(self):
        from mcp_core.context import McpCallContext, with_context

        project, session = uuid.uuid4(), uuid.uuid4()
        server = self._server()

        with with_context(McpCallContext(session_id=session)):
            out = await server.handle_start(
                {"agent_code": "cc", "task_type": "cad", "project_id": str(project)}
            )
            assert bound_project(session) == project
            await server.handle_complete({"session_id": out["session_id"], "status": "completed"})

        assert bound_project(session) is None
