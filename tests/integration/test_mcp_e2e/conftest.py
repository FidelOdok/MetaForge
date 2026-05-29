"""Pytest fixtures for the MCP e2e suite (MET-477 follow-up).

Two run modes share one ``mcp_client`` fixture:

* **In-process** (default): build the FastAPI MCP app against an
  in-memory adapter registry (knowledge / twin / constraint / project /
  memory all populated with stub backends), drive it via
  ``httpx.AsyncClient(transport=ASGITransport(app=...))``. No external
  process, no Postgres, no Neo4j — runs in CI under 10 s.

* **Live** (opt-in): set ``METAFORGE_MCP_URL`` to a running MCP HTTP
  endpoint (e.g. ``http://fidel-dev:8765``). Same fixture, real wire,
  real data. Used for the agent-vertical scenario tests once the live
  data path is populated.

The boundary stays narrow on purpose — every test uses ``rpc()`` /
``call_tool()`` from ``_helpers``; the fixture decides where the
client points.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest


@pytest.fixture(scope="session")
def mcp_live_url() -> str | None:
    """Return ``METAFORGE_MCP_URL`` if set, otherwise None.

    When set, ``mcp_client`` connects to the live server instead of
    spinning up the in-process app. Tests that need known data state
    (a populated KB, real Twin nodes) skip themselves when this is
    None — they're explicitly opt-in to a live environment.
    """
    return os.environ.get("METAFORGE_MCP_URL") or None


@pytest.fixture
async def mcp_client(mcp_live_url: str | None) -> AsyncIterator[httpx.AsyncClient]:
    """Yield an ``httpx.AsyncClient`` bound to either the live or
    in-process MCP HTTP endpoint.

    In-process mode constructs a minimal adapter registry with
    in-memory backends (no Postgres / Neo4j / OpenRouter). Tools that
    need a live backend (knowledge.search against the populated KB,
    memory.retrieve against agent_experiences) will still respond, but
    against empty data — happy-path correctness is verified, semantic
    correctness gets the live mode.
    """
    if mcp_live_url:
        async with httpx.AsyncClient(base_url=mcp_live_url, timeout=60.0) as client:
            yield client
        return

    # In-process — build the FastAPI app against minimal stubs.
    app = await _build_in_process_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://mcp.test") as client:
        yield client


async def _build_in_process_app() -> Any:
    """Construct a ``build_http_app(...)`` FastAPI instance against
    in-memory adapter backends.

    Kept lazy so the import / async-build cost isn't paid by tests
    that only need ``mcp_live_url``.
    """
    from digital_twin.knowledge.embedding_service import create_embedding_service
    from digital_twin.knowledge.store import InMemoryKnowledgeStore
    from digital_twin.memory.client import MemoryClient
    from digital_twin.memory.consolidation import InMemoryInsightStore
    from digital_twin.memory.store import InMemoryExperienceStore
    from metaforge.mcp.__main__ import build_http_app
    from metaforge.mcp.server import build_unified_server
    from twin_core.api import InMemoryTwinAPI

    twin = InMemoryTwinAPI.create()
    embedder = create_embedding_service("local")
    InMemoryKnowledgeStore()  # placeholder — KB service path uses LightRAG in prod

    # ConstraintEngine + ProjectBackend live behind narrow Protocols;
    # the in-process suite uses None and skips the tools that need them.
    memory_store = InMemoryExperienceStore()
    memory_client = MemoryClient(store=memory_store, embeddings=embedder)
    # MET-477 / G1: mirror the live MCP server's insight_store wiring
    # so memory.list_insights returns a clean envelope (empty list)
    # instead of -32001 "set_insight_store was never called".
    insight_store = InMemoryInsightStore()

    server = await build_unified_server(
        knowledge_service=None,  # KB tools require LightRAG — exercised in live mode
        twin=twin,
        constraint_engine=None,
        project_backend=None,
        memory_client=memory_client,
        memory_insight_store=insight_store,
    )
    return build_http_app(server, enable_sse=False, api_key=None)
