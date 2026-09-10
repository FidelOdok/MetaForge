"""Unit tests for ``ExperienceBridgingSessionStore`` (MET-567).

The wrapper is the single seam that catches all three ``complete_session``
callers (REST route, ``session.complete`` MCP tool, sidecar idle rollover), so
what matters is that it stays transparent and that the deposit can never turn
a successful completion into a failure.
"""

from __future__ import annotations

from typing import Any

import pytest

from api_gateway.sessions.backend import InMemoryAgentSessionStore
from api_gateway.sessions.experience_bridge import (
    ExperienceBridgingSessionStore,
    wrap_with_experience_bridge,
)


class RecordingBridge:
    def __init__(self, *, raises: bool = False) -> None:
        self.sessions: list[Any] = []
        self._raises = raises

    async def on_session_completed(self, session: Any) -> bool:
        self.sessions.append(session)
        if self._raises:
            raise RuntimeError("embedder unavailable")
        return True


async def _seeded(store: Any) -> str:
    session = await store.create_session(agent_code="claude-code", task_type="cad")
    await store.append_event(session.id, type="action", message="freecad.create_primitive", data={})
    return str(session.id)


@pytest.mark.asyncio
async def test_completion_deposits_the_completed_session():
    bridge = RecordingBridge()
    store = ExperienceBridgingSessionStore(InMemoryAgentSessionStore(), bridge)
    session_id = await _seeded(store)

    result = await store.complete_session(session_id, status="completed", summary="done")

    assert result.status == "completed"
    # The bridge sees the *completed* session (with its events), not the
    # running one — the summary it embeds depends on the terminal status.
    assert bridge.sessions[0].status == "completed"
    assert len(bridge.sessions[0].events) == 1


@pytest.mark.asyncio
async def test_a_failing_bridge_still_completes_the_session():
    store = ExperienceBridgingSessionStore(
        InMemoryAgentSessionStore(), RecordingBridge(raises=True)
    )
    session_id = await _seeded(store)

    result = await store.complete_session(session_id, status="completed")

    assert result.status == "completed"


@pytest.mark.asyncio
async def test_every_other_operation_delegates_unchanged():
    inner = InMemoryAgentSessionStore()
    store = ExperienceBridgingSessionStore(inner, RecordingBridge())

    session = await store.create_session(
        agent_code="a", task_type="t", title="x", project_id=None, source="external"
    )
    await store.append_event(session.id, type="thought", message="thinking", data={"k": "v"})

    assert (await store.get_session(session.id)) is not None
    assert len(await store.list_sessions()) == 1
    assert await store.abandon_stale_sessions(older_than_seconds=0.0) == 1
    assert store.inner is inner


@pytest.mark.asyncio
async def test_unknown_attributes_pass_through_to_the_inner_store():
    inner = InMemoryAgentSessionStore()
    inner.custom_marker = "kept"  # type: ignore[attr-defined]

    store = ExperienceBridgingSessionStore(inner, RecordingBridge())

    assert store.custom_marker == "kept"


def test_wrap_returns_the_bare_store_when_a_dependency_is_missing():
    inner = InMemoryAgentSessionStore()

    assert wrap_with_experience_bridge(inner, None, object()) is inner
    assert wrap_with_experience_bridge(inner, object(), None) is inner
    assert wrap_with_experience_bridge(None, object(), object()) is None


def test_wrap_installs_the_bridge_when_both_dependencies_are_present():
    class Embeddings:
        async def embed(self, text: str) -> list[float]:
            return [0.0]

    wrapped = wrap_with_experience_bridge(InMemoryAgentSessionStore(), object(), Embeddings())

    assert isinstance(wrapped, ExperienceBridgingSessionStore)
