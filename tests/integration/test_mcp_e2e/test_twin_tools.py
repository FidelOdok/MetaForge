"""Phase 3 — twin.* MCP tools happy-path coverage (MET-477).

Five twin tools exposed by ``tool_registry.tools.twin``:

* ``twin.get_node`` — root node + first-hop neighbours
* ``twin.thread_for`` — depth-bounded subgraph
* ``twin.find_by_property`` — Cypher property lookup (validated label/prop)
* ``twin.constraint_violations`` — engine-evaluated branch state
* ``twin.query_cypher`` — escape-hatch read-only Cypher

In-process mode wires a fresh ``InMemoryTwinAPI`` populated with one
``WorkProduct`` so tests can drive real lookups; live mode pivots the
same fixture at ``METAFORGE_MCP_URL`` (no pre-population on live data).
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import httpx
import pytest

from twin_core.models.enums import WorkProductType
from twin_core.models.work_product import WorkProduct

from ._helpers import McpRpcError, call_tool, rpc

pytestmark = pytest.mark.asyncio


# Cypher (find_by_property, query_cypher read) requires the Neo4j-backed twin.
# In-process tests run against ``InMemoryTwinAPI`` which has no Cypher backend;
# the adapter forwards the call and the in-memory twin raises with a clear
# message. Tests gated on a real Cypher path skip when ``METAFORGE_MCP_URL``
# is unset.
_REQUIRES_LIVE_TWIN = pytest.mark.skipif(
    not os.environ.get("METAFORGE_MCP_URL"),
    reason="needs a Cypher-backed (Neo4j) twin; set METAFORGE_MCP_URL to run live",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_work_product(
    *,
    name: str = "hub_bracket",
    domain: str = "mechanical",
    wp_type: WorkProductType = WorkProductType.CAD_MODEL,
) -> WorkProduct:
    return WorkProduct(
        id=uuid4(),
        name=name,
        type=wp_type,
        domain=domain,
        file_path=f"cad/{name}.step",
        content_hash="deadbeef",
        format="step",
        created_by="test-suite",
    )


@pytest.fixture
async def twin_with_data() -> Any:
    """Fresh ``InMemoryTwinAPI`` populated with one canonical WorkProduct."""
    from twin_core.api import InMemoryTwinAPI

    twin = InMemoryTwinAPI.create()
    wp = _make_work_product()
    await twin.create_work_product(wp)
    # Stash the canonical WP id on the twin so tests can read it back.
    twin.canonical_wp_id = wp.id  # type: ignore[attr-defined]
    twin.canonical_wp_name = wp.name  # type: ignore[attr-defined]
    return twin


@pytest.fixture
async def twin_mcp_client(twin_with_data: Any) -> AsyncIterator[httpx.AsyncClient]:
    """MCP HTTP client whose twin adapter is bound to ``twin_with_data``."""
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

    memory_client = MemoryClient(
        store=InMemoryExperienceStore(),
        embeddings=create_embedding_service("local"),
    )
    server = await build_unified_server(
        knowledge_service=None,
        twin=twin_with_data,
        constraint_engine=None,
        project_backend=None,
        memory_client=memory_client,
        memory_insight_store=InMemoryInsightStore(),
    )
    app = build_http_app(server, enable_sse=False, api_key=None)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://mcp.test") as client:
        yield client


# ---------------------------------------------------------------------------
# twin.get_node
# ---------------------------------------------------------------------------


async def test_get_node_returns_root_plus_first_hop(
    twin_mcp_client: httpx.AsyncClient,
    twin_with_data: Any,
) -> None:
    envelope = await call_tool(
        twin_mcp_client,
        "twin.get_node",
        {"node_id": str(twin_with_data.canonical_wp_id)},
    )
    assert envelope["status"] == "success", envelope
    payload = envelope["data"]
    assert payload["node"] is not None
    assert payload["node"]["id"] == str(twin_with_data.canonical_wp_id)
    # No edges in a fresh single-node graph.
    assert isinstance(payload["neighbours"], list)
    assert isinstance(payload["edges"], list)


async def test_get_node_rejects_invalid_uuid(
    twin_mcp_client: httpx.AsyncClient,
) -> None:
    with pytest.raises(McpRpcError):
        await call_tool(twin_mcp_client, "twin.get_node", {"node_id": "not-a-uuid"})


async def test_get_node_requires_node_id(
    twin_mcp_client: httpx.AsyncClient,
) -> None:
    with pytest.raises(McpRpcError):
        await call_tool(twin_mcp_client, "twin.get_node", {})


# ---------------------------------------------------------------------------
# twin.thread_for
# ---------------------------------------------------------------------------


async def test_thread_for_returns_subgraph_at_depth(
    twin_mcp_client: httpx.AsyncClient,
    twin_with_data: Any,
) -> None:
    envelope = await call_tool(
        twin_mcp_client,
        "twin.thread_for",
        {"node_id": str(twin_with_data.canonical_wp_id), "depth": 2},
    )
    assert envelope["status"] == "success"
    sg = envelope["data"]
    assert "nodes" in sg and "edges" in sg
    node_ids = {n.get("id") for n in sg["nodes"]}
    assert str(twin_with_data.canonical_wp_id) in node_ids


async def test_thread_for_validates_depth_range(
    twin_mcp_client: httpx.AsyncClient,
    twin_with_data: Any,
) -> None:
    with pytest.raises(McpRpcError):
        await call_tool(
            twin_mcp_client,
            "twin.thread_for",
            {"node_id": str(twin_with_data.canonical_wp_id), "depth": 99},
        )


# ---------------------------------------------------------------------------
# twin.find_by_property
# ---------------------------------------------------------------------------


@_REQUIRES_LIVE_TWIN
async def test_find_by_property_matches_canonical_wp(
    twin_mcp_client: httpx.AsyncClient,
    twin_with_data: Any,
) -> None:
    envelope = await call_tool(
        twin_mcp_client,
        "twin.find_by_property",
        {
            "node_type": "WorkProduct",
            "property": "name",
            "value": twin_with_data.canonical_wp_name,
        },
    )
    assert envelope["status"] == "success", envelope
    payload = envelope["data"]
    assert payload["count"] == 1
    assert payload["nodes"][0]["id"] == str(twin_with_data.canonical_wp_id)


async def test_find_by_property_rejects_unsafe_label(
    twin_mcp_client: httpx.AsyncClient,
) -> None:
    """Cypher-unsafe labels are rejected at the adapter (no injection path)."""
    with pytest.raises(McpRpcError):
        await call_tool(
            twin_mcp_client,
            "twin.find_by_property",
            {
                "node_type": "Work Product; DROP DATABASE",
                "property": "name",
                "value": "anything",
            },
        )


async def test_find_by_property_requires_value(
    twin_mcp_client: httpx.AsyncClient,
) -> None:
    with pytest.raises(McpRpcError):
        await call_tool(
            twin_mcp_client,
            "twin.find_by_property",
            {"node_type": "WorkProduct", "property": "name"},
        )


# ---------------------------------------------------------------------------
# twin.constraint_violations
# ---------------------------------------------------------------------------


async def test_constraint_violations_empty_branch_passes(
    twin_mcp_client: httpx.AsyncClient,
) -> None:
    """No constraints registered → evaluation passes, empty violations."""
    envelope = await call_tool(twin_mcp_client, "twin.constraint_violations", {})
    assert envelope["status"] == "success"
    payload = envelope["data"]
    assert payload["passed"] is True
    assert payload["violations"] == []
    assert payload["warnings"] == []
    assert payload["evaluated_count"] >= 0


# ---------------------------------------------------------------------------
# twin.query_cypher
# ---------------------------------------------------------------------------


@_REQUIRES_LIVE_TWIN
async def test_query_cypher_read_returns_rows(
    twin_mcp_client: httpx.AsyncClient,
    twin_with_data: Any,
) -> None:
    envelope = await call_tool(
        twin_mcp_client,
        "twin.query_cypher",
        {
            "cypher": "MATCH (n:WorkProduct) RETURN n LIMIT 5",
            "params": {},
        },
    )
    assert envelope["status"] == "success", envelope
    payload = envelope["data"]
    assert "rows" in payload
    assert payload["count"] >= 1


async def test_query_cypher_rejects_mutations_when_readonly(
    twin_mcp_client: httpx.AsyncClient,
) -> None:
    """Adapter defaults to read-only; CREATE / MERGE / DELETE bounce out."""
    with pytest.raises(McpRpcError):
        await call_tool(
            twin_mcp_client,
            "twin.query_cypher",
            {"cypher": "CREATE (x:Thing {a: 1}) RETURN x"},
        )


async def test_query_cypher_requires_non_empty_cypher(
    twin_mcp_client: httpx.AsyncClient,
) -> None:
    with pytest.raises(McpRpcError):
        await call_tool(twin_mcp_client, "twin.query_cypher", {"cypher": ""})


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------


async def test_twin_tools_appear_in_tools_list(
    twin_mcp_client: httpx.AsyncClient,
) -> None:
    result = await rpc(twin_mcp_client, "tools/list")
    tool_ids = {t.get("name") for t in result.get("tools", [])}
    expected = {
        "twin.get_node",
        "twin.thread_for",
        "twin.find_by_property",
        "twin.constraint_violations",
        "twin.query_cypher",
    }
    missing = expected - tool_ids
    assert not missing, f"missing twin tools: {missing}"
