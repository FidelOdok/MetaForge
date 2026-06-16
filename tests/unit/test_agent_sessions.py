"""Agent-session store + write API (MET-493).

Covers the in-memory ``AgentSessionStore`` and the ``/v1/sessions`` route
handlers (create / append-event / complete, merged list, store-first get)
with an injected in-memory store and a fake workflow engine. Pg store is
exercised by an integration test gated on DATABASE_URL.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api_gateway.sessions import routes
from api_gateway.sessions.backend import (
    InMemoryAgentSessionStore,
    SessionClosedError,
    SessionNotFoundError,
)
from api_gateway.sessions.schemas import (
    SessionCreateRequest,
    SessionEventCreateRequest,
    SessionUpdateRequest,
)


def _req(store=None, engine=None):  # noqa: ANN001, ANN202
    """Minimal fake Request exposing app.state.{agent_session_store,workflow_engine}."""
    state = SimpleNamespace()
    if store is not None:
        state.agent_session_store = store
    if engine is not None:
        state.workflow_engine = engine
    return SimpleNamespace(app=SimpleNamespace(state=state))


class _FakeEngine:
    def __init__(self, runs):  # noqa: ANN001
        self._runs = runs

    async def list_runs(self):  # noqa: ANN202
        return self._runs

    async def get_run(self, run_id):  # noqa: ANN001, ANN202
        return next((r for r in self._runs if r.id == run_id), None)


def _fake_run(run_id: str, started_at: str):
    return SimpleNamespace(
        id=run_id,
        status="completed",
        started_at=started_at,
        completed_at=None,
        step_results={},
    )


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class TestInMemoryStore:
    async def test_create_append_complete_roundtrip(self) -> None:
        store = InMemoryAgentSessionStore.create()
        s = await store.create_session(agent_code="claude-code", task_type="design")
        assert s.status == "running"
        assert s.source == "external"

        for i, etype in enumerate(["thought", "action", "decision"], start=1):
            _eid, seq = await store.append_event(s.id, type=etype, message=f"e{i}")
            assert seq == i

        done = await store.complete_session(s.id, status="completed", summary="done")
        assert done.status == "completed"
        assert done.summary == "done"
        assert done.completed_at is not None

        got = await store.get_session(s.id)
        assert [e.type for e in got.events] == ["thought", "action", "decision"]
        assert [e.message for e in got.events] == ["e1", "e2", "e3"]

    async def test_append_unknown_session_raises(self) -> None:
        store = InMemoryAgentSessionStore.create()
        with pytest.raises(SessionNotFoundError):
            await store.append_event("nope", type="thought", message="x")

    async def test_append_after_complete_raises(self) -> None:
        store = InMemoryAgentSessionStore.create()
        s = await store.create_session(agent_code="a", task_type="t")
        await store.complete_session(s.id, status="completed")
        with pytest.raises(SessionClosedError):
            await store.append_event(s.id, type="thought", message="late")

    async def test_double_complete_raises(self) -> None:
        store = InMemoryAgentSessionStore.create()
        s = await store.create_session(agent_code="a", task_type="t")
        await store.complete_session(s.id, status="completed")
        with pytest.raises(SessionClosedError):
            await store.complete_session(s.id, status="failed")

    async def test_list_sessions_filters_by_project(self) -> None:
        """MET-516: list_sessions(project_id) scopes to one project."""
        store = InMemoryAgentSessionStore.create()
        a = await store.create_session(agent_code="a", task_type="t", project_id="p1")
        await store.create_session(agent_code="b", task_type="t", project_id="p2")
        await store.create_session(agent_code="c", task_type="t")  # unscoped
        scoped = await store.list_sessions("p1")
        assert [s.id for s in scoped] == [a.id]
        assert scoped[0].project_id == "p1"
        assert len(await store.list_sessions()) == 3  # no filter → all

    async def test_abandon_stale_sessions(self) -> None:
        """MET-510: old running sessions are retired; fresh/closed ones aren't."""
        store = InMemoryAgentSessionStore.create()
        stale = await store.create_session(agent_code="a", task_type="t")
        # Backdate it past the cutoff.
        store._sessions[stale.id].started_at = "2020-01-01T00:00:00+00:00"
        fresh = await store.create_session(agent_code="a", task_type="t")
        done = await store.create_session(agent_code="a", task_type="t")
        store._sessions[done.id].started_at = "2020-01-01T00:00:00+00:00"
        await store.complete_session(done.id, status="completed")

        n = await store.abandon_stale_sessions(older_than_seconds=3600)
        assert n == 1
        assert store._sessions[stale.id].status == "abandoned"
        assert store._sessions[stale.id].completed_at is not None
        assert store._sessions[fresh.id].status == "running"  # too recent
        assert store._sessions[done.id].status == "completed"  # already terminal


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


class TestRoutes:
    async def test_create_event_complete_flow(self) -> None:
        store = InMemoryAgentSessionStore.create()
        req = _req(store=store)

        session = await routes.create_session(
            SessionCreateRequest(agent_code="claude-code", task_type="design"), req
        )
        assert session.status == "running"

        ack = await routes.append_session_event(
            session.id, SessionEventCreateRequest(type="thought", message="hmm"), req
        )
        assert ack.seq == 1

        done = await routes.update_session(
            session.id, SessionUpdateRequest(status="completed", summary="s"), req
        )
        assert done.status == "completed"

    async def test_double_complete_returns_409(self) -> None:
        store = InMemoryAgentSessionStore.create()
        req = _req(store=store)
        s = await routes.create_session(SessionCreateRequest(agent_code="a", task_type="t"), req)
        await routes.update_session(s.id, SessionUpdateRequest(status="completed"), req)
        with pytest.raises(HTTPException) as exc:
            await routes.update_session(s.id, SessionUpdateRequest(status="failed"), req)
        assert exc.value.status_code == 409

    async def test_event_on_unknown_session_404(self) -> None:
        req = _req(store=InMemoryAgentSessionStore.create())
        with pytest.raises(HTTPException) as exc:
            await routes.append_session_event(
                "missing", SessionEventCreateRequest(type="thought", message="x"), req
            )
        assert exc.value.status_code == 404

    async def test_bad_project_id_400(self) -> None:
        req = _req(store=InMemoryAgentSessionStore.create())
        with pytest.raises(HTTPException) as exc:
            await routes.create_session(
                SessionCreateRequest(agent_code="a", task_type="t", project_id="not-a-uuid"),
                req,
            )
        assert exc.value.status_code == 400

    async def test_store_absent_503(self) -> None:
        req = _req()  # no store on app.state
        with pytest.raises(HTTPException) as exc:
            await routes.create_session(SessionCreateRequest(agent_code="a", task_type="t"), req)
        assert exc.value.status_code == 503


class TestMergedList:
    async def test_list_merges_workflow_and_external_sorted_desc(self) -> None:
        store = InMemoryAgentSessionStore.create()
        ext = await store.create_session(agent_code="claude-code", task_type="design")
        engine = _FakeEngine([_fake_run("run-1", "2026-01-01T00:00:00+00:00")])

        resp = await routes.list_sessions(_req(store=store, engine=engine))

        assert resp.total == 2
        ids = {s.id for s in resp.sessions}
        assert ids == {ext.id, "run-1"}
        # external session is more recent (now) → sorts first; list is desc
        starts = [s.started_at for s in resp.sessions]
        assert starts == sorted(starts, reverse=True)
        external = next(s for s in resp.sessions if s.id == ext.id)
        assert external.source == "external"

    async def test_get_resolves_store_first_then_engine(self) -> None:
        store = InMemoryAgentSessionStore.create()
        ext = await store.create_session(agent_code="a", task_type="t")
        engine = _FakeEngine([_fake_run("run-1", "2026-01-01T00:00:00+00:00")])
        req = _req(store=store, engine=engine)

        from_store = await routes.get_session(ext.id, req)
        assert from_store.source == "external"

        from_engine = await routes.get_session("run-1", req)
        assert from_engine.id == "run-1"

        with pytest.raises(HTTPException) as exc:
            await routes.get_session("ghost", req)
        assert exc.value.status_code == 404
