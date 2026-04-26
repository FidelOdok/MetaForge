"""Integration tests for PostgreSQL gateway wiring (MET-305).

Opt in with ``pytest --integration``. Requires the ``metaforge-postgres-1``
container running and reachable at ``localhost:5432`` with the default
``metaforge`` / ``metaforge`` credentials (matches the dev
``docker-compose.yml``).

These tests prove four things:

* ``create_app()`` boots cleanly when ``DATABASE_URL`` is set, and the
  resulting chat / project backends are the PostgreSQL implementations
  (not the in-memory fallbacks).
* The schema is created on first boot and a re-boot is idempotent —
  ``Base.metadata.create_all`` does not raise on existing tables.
* Project records survive a fresh app boot — proves the persistence
  acceptance.
* The ``/health`` endpoint reports a ``postgres`` dependency.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.integration


_DEFAULT_DSN = "postgresql+asyncpg://metaforge:metaforge@localhost:5432/metaforge"


def _dsn() -> str:
    return os.environ.get("DATABASE_URL", _DEFAULT_DSN)


@pytest.fixture(autouse=True)
def _postgres_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force Postgres on for every test in this module."""
    monkeypatch.setenv("DATABASE_URL", _dsn())


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """One ASGI client per test, with a fully booted gateway lifespan."""
    from api_gateway.db.engine import dispose_engine
    from api_gateway.server import create_app

    # The engine is a module-level singleton — drop any cached engine
    # from a prior test before booting the new app.
    await dispose_engine()
    app = create_app()
    transport = ASGITransport(app=app, raise_app_exceptions=True)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        async with app.router.lifespan_context(app):
            yield ac


# ---------------------------------------------------------------------------
# Boot tests
# ---------------------------------------------------------------------------


class TestStartup:
    async def test_chat_backend_uses_postgres(self) -> None:
        from api_gateway.chat import routes as chat_routes
        from api_gateway.db.engine import dispose_engine
        from api_gateway.server import create_app

        await dispose_engine()
        app = create_app()
        async with app.router.lifespan_context(app):
            backend = chat_routes._backend  # noqa: SLF001
            assert backend is not None, "chat backend not initialized"
            assert type(backend).__name__ == "PgChatBackend", (
                f"expected PgChatBackend, got {type(backend).__name__}"
            )

    async def test_project_backend_uses_postgres(self) -> None:
        from api_gateway.db.engine import dispose_engine
        from api_gateway.projects import routes as project_routes
        from api_gateway.server import create_app

        await dispose_engine()
        app = create_app()
        async with app.router.lifespan_context(app):
            backend = project_routes._backend  # noqa: SLF001
            assert backend is not None, "project backend not initialized"
            assert type(backend).__name__ == "PgProjectBackend", (
                f"expected PgProjectBackend, got {type(backend).__name__}"
            )

    async def test_schema_create_is_idempotent(self) -> None:
        """Booting the app twice in succession must not raise."""
        from api_gateway.db.engine import dispose_engine
        from api_gateway.server import create_app

        for _ in range(2):
            await dispose_engine()
            app = create_app()
            async with app.router.lifespan_context(app):
                pass  # boot + shutdown only

    async def test_health_endpoint_lists_postgres(self, client: AsyncClient) -> None:
        response = await client.get("/health")
        assert response.status_code in {200, 503}, response.text
        body = response.json()
        component_names = {c.get("name") for c in body.get("components", [])}
        assert "postgres" in component_names, body


# ---------------------------------------------------------------------------
# Persistence test
# ---------------------------------------------------------------------------


class TestPersistence:
    async def test_project_survives_restart(self) -> None:
        """A project written through one app instance is readable from a
        second app instance pointed at the same Postgres.
        """
        from api_gateway.db.engine import dispose_engine
        from api_gateway.projects import routes as project_routes
        from api_gateway.server import create_app

        sentinel_name = f"met305-persist-{uuid4().hex[:8]}"
        project_id: str

        # Phase 1 — write through the project backend
        await dispose_engine()
        app1 = create_app()
        async with app1.router.lifespan_context(app1):
            backend = project_routes._backend  # noqa: SLF001
            assert backend is not None
            created = await backend.create_project(
                name=sentinel_name,
                description="MET-305 persistence test",
                status="draft",
            )
            project_id = created.id

        # Phase 2 — read from a fresh app
        await dispose_engine()
        app2 = create_app()
        async with app2.router.lifespan_context(app2):
            backend = project_routes._backend  # noqa: SLF001
            assert backend is not None
            fetched = await backend.get_project(project_id)
            assert fetched is not None, "project did not persist"
            assert fetched.name == sentinel_name

            # Cleanup so the dev DB stays uncluttered.
            await backend.delete_project(project_id)

    async def test_chat_thread_survives_restart(self) -> None:
        """Chat threads + messages persist across a fresh app boot."""
        from api_gateway.chat import routes as chat_routes
        from api_gateway.db.engine import dispose_engine
        from api_gateway.server import create_app

        thread_id: str
        sentinel_msg = f"met305-chat-{uuid4().hex[:8]}"

        # Phase 1 — write a thread + message
        await dispose_engine()
        app1 = create_app()
        async with app1.router.lifespan_context(app1):
            backend = chat_routes._backend  # noqa: SLF001
            assert backend is not None
            channels = await backend.list_channels()
            assert channels, "default channels were not seeded"
            channel = channels[0]
            thread = await backend.create_thread(
                channel_id=channel.id,
                scope_kind=channel.scope_kind,
                scope_entity_id=str(uuid4()),
                title=f"persist-test-{uuid4().hex[:8]}",
            )
            thread_id = thread.id
            await backend.add_message(
                thread_id=thread_id,
                actor_id="test",
                actor_kind="user",
                content=sentinel_msg,
            )

        # Phase 2 — re-boot and confirm the message is still there
        await dispose_engine()
        app2 = create_app()
        async with app2.router.lifespan_context(app2):
            backend = chat_routes._backend  # noqa: SLF001
            assert backend is not None
            messages = await backend.get_messages(thread_id)
            assert any(m.content == sentinel_msg for m in messages), (
                f"sentinel message missing; saw {[m.content for m in messages]}"
            )
