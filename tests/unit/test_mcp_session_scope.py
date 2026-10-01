"""Picking a project has to make the scope stick (FORGE-334).

``/metaforge:use`` calls ``session.start`` with a project_id and the server
answers ``project_scope_bound``. Two things were wrong with that.

**It reported true when it could not be true.** The binding is keyed on the
call context's session_id, and a caller presenting no session identity gets
a freshly generated one per call -- so the binding was stored against an id
nothing would ever present again. ``_bind_project`` returned True anyway.
That is worse than a plain bug, because the server's own instructions tell
the agent to keep passing ``project_id`` explicitly *only* when the flag
comes back false. A true answer is an instruction to stop sending the one
thing that still worked.

**HTTP clients had no way to have a stable session.** The Streamable HTTP
transport defines one: a server MAY assign ``Mcp-Session-Id`` on the
InitializeResult, and a client that receives one MUST echo it on every
later request. We issued none, so only stdio and clients taught our own
``X-MetaForge-Session`` header could hold a scope at all.
"""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest

from mcp_core.context import (
    HEADER_SESSION,
    McpCallContext,
    bound_project,
    context_from_env,
    context_from_headers,
    current_context,
    reset_session_projects,
    with_context,
)


@pytest.fixture(autouse=True)
def _clean_bindings():
    reset_session_projects()
    yield
    reset_session_projects()


def _session_server():
    from api_gateway.sessions.backend import InMemoryAgentSessionStore
    from tool_registry.tools.session.adapter import SessionServer

    return SessionServer(store=InMemoryAgentSessionStore())


# ---------------------------------------------------------------------------
# Knowing whether a session is ours or invented
# ---------------------------------------------------------------------------


def test_a_supplied_session_is_stable() -> None:
    sid = uuid.uuid4()
    assert McpCallContext(session_id=sid).session_is_stable is True
    assert context_from_headers({HEADER_SESSION: str(sid)}).session_is_stable is True
    assert context_from_env({"METAFORGE_SESSION_ID": str(sid)}).session_is_stable is True


def test_an_invented_session_is_not() -> None:
    assert McpCallContext().session_is_stable is False
    assert context_from_headers({}).session_is_stable is False
    assert context_from_env({}).session_is_stable is False


def test_the_unattributed_sentinel_is_not_stable() -> None:
    """Every call with no context installed shares the all-zero session.

    Binding to it would be a process-global "last project wins" under another
    name -- the exact leak the per-session registry exists to prevent, and on
    a shared sidecar it would hand one client's project to another.
    """
    from mcp_core.context import _DEFAULT_CONTEXT

    assert _DEFAULT_CONTEXT.session_is_stable is False


def test_copying_a_context_keeps_the_answer() -> None:
    """The HTTP path does exactly this when OAuth resolves an actor."""
    ctx = McpCallContext(session_id=uuid.uuid4())
    assert ctx.model_copy(update={"actor_id": "user:fidel"}).session_is_stable is True


# ---------------------------------------------------------------------------
# session.start reports what actually happened
# ---------------------------------------------------------------------------


def test_bound_is_only_claimed_when_it_will_be_found_again() -> None:
    project = uuid.uuid4()
    server = _session_server()

    # A plain HTTP client that sends no session header at all.
    with with_context(context_from_headers({})):
        out = asyncio.run(
            server.handle_start(
                {"agent_code": "cc", "task_type": "cad", "project_id": str(project)}
            )
        )
    assert out["project_scope_bound"] is False

    # And the next call from that client really does see no scope, which is
    # what makes the False the truthful answer rather than a pessimistic one.
    with with_context(context_from_headers({})):
        assert bound_project(current_context().session_id) is None


def test_a_stable_session_still_binds() -> None:
    project, session = uuid.uuid4(), uuid.uuid4()
    server = _session_server()
    with with_context(context_from_headers({HEADER_SESSION: str(session)})):
        out = asyncio.run(
            server.handle_start(
                {"agent_code": "cc", "task_type": "cad", "project_id": str(project)}
            )
        )
    assert out["project_scope_bound"] is True
    assert bound_project(session) == project


def test_the_scope_reaches_a_later_call_on_the_same_session() -> None:
    """The point of the whole feature: pick once, and later calls that name
    no project resolve to it."""
    project, session = uuid.uuid4(), uuid.uuid4()
    server = _session_server()
    headers = {HEADER_SESSION: str(session)}
    with with_context(context_from_headers(headers)):
        asyncio.run(
            server.handle_start(
                {"agent_code": "cc", "task_type": "cad", "project_id": str(project)}
            )
        )
    later = context_from_headers(headers)
    assert later.project_id == project


def test_an_explicit_project_still_wins_over_the_session_scope() -> None:
    project, other, session = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    server = _session_server()
    with with_context(context_from_headers({HEADER_SESSION: str(session)})):
        asyncio.run(
            server.handle_start(
                {"agent_code": "cc", "task_type": "cad", "project_id": str(project)}
            )
        )
    named = context_from_headers({HEADER_SESSION: str(session), "X-MetaForge-Project": str(other)})
    assert named.project_id == other


# ---------------------------------------------------------------------------
# Mcp-Session-Id: the session every compliant HTTP client already echoes
# ---------------------------------------------------------------------------


def _app():
    pytest.importorskip("starlette.testclient")
    from metaforge.mcp.__main__ import build_http_app

    class _FakeServer:
        adapters: dict = {}
        tool_ids: list = []

        def declare_auth_posture(self, posture) -> None:
            pass

        # FORGE-387: the transport declares who is on the other end,
        # so a stand-in for the server has to be able to hear it.
        def declare_caller(self, caller) -> None:
            pass

        # FORGE-423: and the elicitor it attaches. `build_http_app` calls
        # this unconditionally rather than guarding with getattr, because a
        # server that cannot accept one would silently lose inline approvals
        # -- which is the bug FORGE-423 fixed, and not one to reintroduce as
        # a kindness to a test double.
        def attach_elicitor(self, elicitor) -> None:
            pass

        async def handle_request(self, raw: str) -> str:
            message = json.loads(raw)
            return json.dumps({"jsonrpc": "2.0", "id": message.get("id"), "result": {}})

    return build_http_app(_FakeServer(), enable_sse=False)


def _client():
    from starlette.testclient import TestClient

    return TestClient(_app())


def test_initialize_is_answered_with_a_session_id() -> None:
    resp = _client().post(
        "/mcp", json={"jsonrpc": "2.0", "id": "1", "method": "initialize", "params": {}}
    )
    assert resp.status_code == 200
    assigned = resp.headers.get("mcp-session-id")
    assert assigned, "no Mcp-Session-Id issued, so no client can hold a scope"
    uuid.UUID(assigned)  # must be parseable: bindings are keyed by UUID


def test_an_ordinary_call_is_not_given_a_session_it_never_asked_for() -> None:
    """Only ``initialize`` mints one. Handing a session to a client that is
    not in one would look like a scope it does not actually have."""
    resp = _client().post(
        "/mcp", json={"jsonrpc": "2.0", "id": "1", "method": "tools/list", "params": {}}
    )
    assert "mcp-session-id" not in {k.lower() for k in resp.headers}


def test_an_echoed_session_becomes_the_call_context() -> None:
    """The whole chain: the id the server issued arrives back on a later
    request and resolves to the project bound to it."""
    project = uuid.uuid4()
    client = _client()
    assigned = client.post(
        "/mcp", json={"jsonrpc": "2.0", "id": "1", "method": "initialize", "params": {}}
    ).headers["mcp-session-id"]

    from mcp_core.context import bind_session_project

    bind_session_project(uuid.UUID(assigned), project)

    ctx = context_from_headers({"Mcp-Session-Id": assigned})
    # context_from_headers does not know about Mcp-Session-Id; the HTTP layer
    # translates it. Assert the translation itself.
    from metaforge.mcp.__main__ import _session_for_request

    resolved = _session_for_request({"Mcp-Session-Id": assigned}, b"{}")
    assert resolved == assigned
    assert context_from_headers({HEADER_SESSION: resolved}).project_id == project
    assert ctx is not None


def test_an_unparseable_session_header_is_refused_not_guessed() -> None:
    """Our registry keys on UUIDs. A value we cannot parse would become
    "no session" one layer down, which reads as a client that sent nothing."""
    from metaforge.mcp.__main__ import _session_for_request

    assert _session_for_request({"Mcp-Session-Id": "not-a-uuid"}, b"{}") is None


def test_a_malformed_initialize_body_mints_nothing() -> None:
    from metaforge.mcp.__main__ import _session_for_request

    assert _session_for_request({}, b"{not json") is None


def test_delete_terminates_the_session_and_releases_the_scope() -> None:
    from mcp_core.context import bind_session_project

    project, session = uuid.uuid4(), uuid.uuid4()
    bind_session_project(session, project)
    resp = _client().request("DELETE", "/mcp", headers={"Mcp-Session-Id": str(session)})
    assert resp.status_code == 204
    assert bound_project(session) is None


def test_delete_without_a_session_is_a_bad_request() -> None:
    assert _client().request("DELETE", "/mcp").status_code == 400
