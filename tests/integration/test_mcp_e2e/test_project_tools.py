"""Phase 3 — project.* MCP tools happy-path coverage (MET-477).

Three project tools exposed by ``tool_registry.tools.project``:

* ``project.create`` — persists a new project, returns id + timestamps
* ``project.list``   — returns every project the caller can see
* ``project.get``    — fetch by id or by exact name; null when missing

In-process mode wires ``api_gateway.projects.backend.InMemoryProjectBackend``
which already satisfies the adapter's ``ProjectBackendLike`` Protocol.
Live mode pivots the same fixture at ``METAFORGE_MCP_URL``.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from ._helpers import McpRpcError, call_tool, rpc

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fixture: MCP HTTP client with the project adapter wired to an
# InMemoryProjectBackend.
# ---------------------------------------------------------------------------


@pytest.fixture
async def project_backend() -> Any:
    from api_gateway.projects.backend import InMemoryProjectBackend

    return InMemoryProjectBackend.create()


@pytest.fixture
async def project_mcp_client(project_backend: Any) -> AsyncIterator[httpx.AsyncClient]:
    live_url = os.environ.get("METAFORGE_MCP_URL") or None
    if live_url:
        async with httpx.AsyncClient(base_url=live_url, timeout=60.0) as client:
            yield client
        return

    from digital_twin.knowledge.embedding_service import create_embedding_service
    from digital_twin.memory.client import MemoryClient
    from digital_twin.memory.consolidation import InMemoryInsightStore
    from digital_twin.memory.store import InMemoryExperienceStore
    from metaforge.mcp.__main__ import build_http_app
    from metaforge.mcp.server import build_unified_server
    from twin_core.api import InMemoryTwinAPI

    memory_client = MemoryClient(
        store=InMemoryExperienceStore(),
        embeddings=create_embedding_service("local"),
    )
    server = await build_unified_server(
        knowledge_service=None,
        twin=InMemoryTwinAPI.create(),
        constraint_engine=None,
        project_backend=project_backend,
        memory_client=memory_client,
        memory_insight_store=InMemoryInsightStore(),
    )
    app = build_http_app(server, enable_sse=False, api_key=None)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://mcp.test") as client:
        yield client


# ---------------------------------------------------------------------------
# project.create
# ---------------------------------------------------------------------------


async def test_project_create_returns_persisted_record(
    project_mcp_client: httpx.AsyncClient,
    project_backend: Any,
) -> None:
    envelope = await call_tool(
        project_mcp_client,
        "project.create",
        {
            "name": "STEM Drone Demo",
            "description": "Classroom drone reference project",
            "status": "draft",
        },
    )
    assert envelope["status"] == "success", envelope
    project = envelope["data"]
    assert project["name"] == "STEM Drone Demo"
    assert project["status"] == "draft"
    assert project["description"] == "Classroom drone reference project"
    assert isinstance(project["id"], str) and len(project["id"]) > 0

    # Persisted in the backend, not just echoed back.
    stored = await project_backend.get_project(project["id"])
    assert stored is not None
    assert stored.name == "STEM Drone Demo"


async def test_project_create_requires_name(
    project_mcp_client: httpx.AsyncClient,
) -> None:
    with pytest.raises(McpRpcError):
        await call_tool(project_mcp_client, "project.create", {})

    with pytest.raises(McpRpcError):
        await call_tool(project_mcp_client, "project.create", {"name": ""})


# ---------------------------------------------------------------------------
# project.list
# ---------------------------------------------------------------------------


async def test_project_list_empty(
    project_mcp_client: httpx.AsyncClient,
) -> None:
    envelope = await call_tool(project_mcp_client, "project.list", {})
    assert envelope["status"] == "success", envelope
    payload = envelope["data"]
    assert payload["total"] == 0
    assert payload["projects"] == []


async def test_project_list_returns_created_projects(
    project_mcp_client: httpx.AsyncClient,
) -> None:
    await call_tool(project_mcp_client, "project.create", {"name": "Alpha"})
    await call_tool(project_mcp_client, "project.create", {"name": "Beta"})

    envelope = await call_tool(project_mcp_client, "project.list", {})
    payload = envelope["data"]
    assert payload["total"] == 2
    names = {p["name"] for p in payload["projects"]}
    assert names == {"Alpha", "Beta"}


# ---------------------------------------------------------------------------
# project.get
# ---------------------------------------------------------------------------


async def test_project_get_by_id_round_trip(
    project_mcp_client: httpx.AsyncClient,
) -> None:
    created = await call_tool(
        project_mcp_client,
        "project.create",
        {"name": "Gamma"},
    )
    project_id = created["data"]["id"]

    envelope = await call_tool(
        project_mcp_client,
        "project.get",
        {"id": project_id},
    )
    assert envelope["status"] == "success"
    fetched = envelope["data"]
    assert fetched is not None
    assert fetched["id"] == project_id
    assert fetched["name"] == "Gamma"


async def test_project_get_by_name(
    project_mcp_client: httpx.AsyncClient,
) -> None:
    await call_tool(project_mcp_client, "project.create", {"name": "Delta"})

    envelope = await call_tool(
        project_mcp_client,
        "project.get",
        {"name": "Delta"},
    )
    assert envelope["status"] == "success"
    assert envelope["data"]["name"] == "Delta"


async def test_project_get_missing_returns_null(
    project_mcp_client: httpx.AsyncClient,
) -> None:
    """Unknown id → tool returns null payload (not an error envelope)."""
    envelope = await call_tool(
        project_mcp_client,
        "project.get",
        {"id": "00000000-0000-0000-0000-000000000000"},
    )
    assert envelope["status"] == "success"
    assert envelope["data"] is None


async def test_project_get_requires_id_or_name(
    project_mcp_client: httpx.AsyncClient,
) -> None:
    with pytest.raises(McpRpcError):
        await call_tool(project_mcp_client, "project.get", {})


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------


async def test_project_tools_appear_in_tools_list(
    project_mcp_client: httpx.AsyncClient,
) -> None:
    result = await rpc(project_mcp_client, "tools/list")
    tool_ids = {t.get("name") for t in result.get("tools", [])}
    expected = {"project.create", "project.list", "project.get"}
    missing = expected - tool_ids
    assert not missing, f"missing project tools: {missing}"
