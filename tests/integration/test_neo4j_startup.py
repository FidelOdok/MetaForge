"""Integration tests for Neo4j gateway wiring (MET-304).

Opt in with ``pytest --integration``. Requires the ``metaforge-neo4j-1``
container running and reachable at ``bolt://localhost:7687`` with the
default ``neo4j`` / ``metaforge`` credentials (matches the dev
``docker-compose.yml``).

These tests prove three things:

* ``create_app()`` boots cleanly when ``NEO4J_URI`` is set, and the
  resulting Twin uses ``Neo4jGraphEngine`` (not the in-memory fallback).
* Work products created via the HTTP API survive a fresh app boot —
  the proof of "data persists across restarts" from MET-292's parent
  story.
* The ``/health`` endpoint reports Neo4j as a registered dependency.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.integration


_DEFAULT_URI = "bolt://localhost:7687"
_DEFAULT_USER = "neo4j"
_DEFAULT_PASSWORD = "metaforge"


def _neo4j_env() -> dict[str, str]:
    return {
        "NEO4J_URI": os.environ.get("NEO4J_URI", _DEFAULT_URI),
        "NEO4J_USER": os.environ.get("NEO4J_USER", _DEFAULT_USER),
        "NEO4J_PASSWORD": os.environ.get("NEO4J_PASSWORD", _DEFAULT_PASSWORD),
    }


@pytest.fixture(autouse=True)
def _neo4j_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force Neo4j on for every test in this module."""
    for key, value in _neo4j_env().items():
        monkeypatch.setenv(key, value)


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """One ASGI client per test, with a fully booted gateway lifespan."""
    # Imported here so the env vars set above are visible to module-level
    # OTel + settings reads inside ``create_app``.
    from api_gateway.server import create_app

    app = create_app()
    transport = ASGITransport(app=app, raise_app_exceptions=True)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Trigger lifespan startup so ``_init_orchestrator`` runs.
        async with app.router.lifespan_context(app):
            yield ac


# ---------------------------------------------------------------------------
# Boot tests
# ---------------------------------------------------------------------------


class TestStartup:
    async def test_twin_uses_neo4j_backend(self, client: AsyncClient) -> None:
        """The bound Twin must be backed by Neo4j when NEO4J_URI is set."""
        from api_gateway.server import create_app

        app = create_app()
        async with app.router.lifespan_context(app):
            twin = app.state.twin
            graph_cls = type(twin._graph).__name__  # noqa: SLF001
            assert graph_cls == "Neo4jGraphEngine", f"expected Neo4jGraphEngine, got {graph_cls}"

    async def test_health_endpoint_lists_neo4j(self, client: AsyncClient) -> None:
        response = await client.get("/health")
        assert response.status_code in {200, 503}, response.text
        body = response.json()
        component_names = {c.get("name") for c in body.get("components", [])}
        assert "neo4j" in component_names, body


# ---------------------------------------------------------------------------
# Persistence test
# ---------------------------------------------------------------------------


class TestPersistence:
    async def test_work_product_survives_restart(self) -> None:
        """A work product written through one app instance must be
        readable from a second app instance pointed at the same Neo4j.
        """
        from api_gateway.server import create_app
        from twin_core.models.enums import WorkProductType
        from twin_core.models.work_product import WorkProduct

        wp_id = uuid4()
        sentinel_name = f"met304-persist-{wp_id.hex[:8]}"

        # Phase 1 — write
        app1 = create_app()
        async with app1.router.lifespan_context(app1):
            twin = app1.state.twin
            await twin.create_work_product(
                WorkProduct(
                    id=wp_id,
                    name=sentinel_name,
                    type=WorkProductType.CAD_MODEL,
                    domain="mechanical",
                    file_path="cad/met304.step",
                    content_hash=wp_id.hex,
                    format="step",
                    created_by="met-304-test",
                )
            )

        # Phase 2 — read from a fresh app
        app2 = create_app()
        async with app2.router.lifespan_context(app2):
            twin = app2.state.twin
            fetched = await twin.get_work_product(wp_id)
            assert fetched is not None, "work product did not persist"
            assert fetched.name == sentinel_name

            # Cleanup so the dev DB stays uncluttered.
            await twin.delete_work_product(wp_id)
