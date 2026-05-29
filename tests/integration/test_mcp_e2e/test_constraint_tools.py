"""Phase 3 — constraint.* MCP tools happy-path coverage (MET-477).

One tool exposed by ``tool_registry.tools.constraint``:

* ``constraint.validate`` — pre-flight constraint evaluation for a set
  of work_products. Returns ``passed`` + ``violations`` + ``warnings``
  + ``evaluated_count`` + ``skipped_count`` + ``duration_ms``.

In-process mode wires ``InMemoryTwinAPI``'s in-memory constraint engine
(``twin._constraints``) into ``build_unified_server``. Live mode pivots
the same fixture at ``METAFORGE_MCP_URL``.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import httpx
import pytest

from twin_core.models.constraint import Constraint
from twin_core.models.enums import ConstraintSeverity, WorkProductType
from twin_core.models.work_product import WorkProduct

from ._helpers import McpRpcError, call_tool, rpc

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def twin_with_constraint() -> Any:
    """``InMemoryTwinAPI`` pre-populated with one WorkProduct + one PASS constraint."""
    from twin_core.api import InMemoryTwinAPI

    twin = InMemoryTwinAPI.create()

    wp = WorkProduct(
        id=uuid4(),
        name="hub_bracket",
        type=WorkProductType.CAD_MODEL,
        domain="mechanical",
        file_path="cad/hub_bracket.step",
        content_hash="deadbeef",
        format="step",
        created_by="test-suite",
    )
    await twin.create_work_product(wp)

    constraint = Constraint(
        name="trivial_pass_rule",
        expression="True",
        severity=ConstraintSeverity.ERROR,
        domain="mechanical",
        source="test-suite",
        message="trivial rule that always passes",
    )
    await twin.constraints.add_constraint(constraint, [wp.id])

    twin.canonical_wp_id = wp.id  # type: ignore[attr-defined]
    twin.canonical_constraint_id = constraint.id  # type: ignore[attr-defined]
    return twin


@pytest.fixture
async def constraint_mcp_client(
    twin_with_constraint: Any,
) -> AsyncIterator[httpx.AsyncClient]:
    """MCP client wired to the in-memory engine of ``twin_with_constraint``."""
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
        twin=twin_with_constraint,
        constraint_engine=twin_with_constraint.constraints,
        project_backend=None,
        memory_client=memory_client,
        memory_insight_store=InMemoryInsightStore(),
    )
    app = build_http_app(server, enable_sse=False, api_key=None)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://mcp.test") as client:
        yield client


# ---------------------------------------------------------------------------
# constraint.validate — happy path
# ---------------------------------------------------------------------------


async def test_validate_empty_list_returns_zero_evaluated(
    constraint_mcp_client: httpx.AsyncClient,
) -> None:
    """Empty input → engine evaluates nothing, passes vacuously."""
    envelope = await call_tool(
        constraint_mcp_client,
        "constraint.validate",
        {"work_product_ids": []},
    )
    assert envelope["status"] == "success", envelope
    payload = envelope["data"]
    assert payload["passed"] is True
    assert payload["evaluated_count"] == 0
    assert payload["violations"] == []
    assert payload["warnings"] == []
    assert payload["duration_ms"] >= 0.0


async def test_validate_with_work_product_evaluates_attached_constraint(
    constraint_mcp_client: httpx.AsyncClient,
    twin_with_constraint: Any,
) -> None:
    """When the WP has a constraint attached, the engine evaluates it."""
    envelope = await call_tool(
        constraint_mcp_client,
        "constraint.validate",
        {"work_product_ids": [str(twin_with_constraint.canonical_wp_id)]},
    )
    assert envelope["status"] == "success", envelope
    payload = envelope["data"]
    assert payload["passed"] is True
    assert payload["evaluated_count"] == 1
    assert payload["violations"] == []


async def test_validate_with_unknown_work_product_evaluates_none(
    constraint_mcp_client: httpx.AsyncClient,
) -> None:
    """Unknown WP id resolves to zero constraints — vacuous pass."""
    envelope = await call_tool(
        constraint_mcp_client,
        "constraint.validate",
        {"work_product_ids": [str(uuid4())]},
    )
    payload = envelope["data"]
    assert payload["passed"] is True
    assert payload["evaluated_count"] == 0


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


async def test_validate_requires_work_product_ids(
    constraint_mcp_client: httpx.AsyncClient,
) -> None:
    """Missing ``work_product_ids`` raises a clean error envelope."""
    with pytest.raises(McpRpcError):
        await call_tool(constraint_mcp_client, "constraint.validate", {})


async def test_validate_rejects_non_uuid_strings(
    constraint_mcp_client: httpx.AsyncClient,
) -> None:
    """Non-UUID strings surface as a clean ValueError → JSON-RPC error."""
    with pytest.raises(McpRpcError):
        await call_tool(
            constraint_mcp_client,
            "constraint.validate",
            {"work_product_ids": ["not-a-uuid"]},
        )


async def test_validate_rejects_non_list_input(
    constraint_mcp_client: httpx.AsyncClient,
) -> None:
    """``work_product_ids`` must be a list — string or object rejected."""
    with pytest.raises(McpRpcError):
        await call_tool(
            constraint_mcp_client,
            "constraint.validate",
            {"work_product_ids": "not-a-list"},
        )


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------


async def test_constraint_tool_appears_in_tools_list(
    constraint_mcp_client: httpx.AsyncClient,
) -> None:
    result = await rpc(constraint_mcp_client, "tools/list")
    tool_ids = {t.get("name") for t in result.get("tools", [])}
    assert "constraint.validate" in tool_ids, (
        f"missing constraint.validate: {sorted(t for t in tool_ids if t)}"
    )
