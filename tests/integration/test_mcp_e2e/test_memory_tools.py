"""Memory MCP tools — happy path against the in-process server (MET-477 / G1).

The pre-G1 behaviour was: ``memory.list_insights`` raised -32001 with
``"MemoryServer.insight_store was called before set_insight_store()"``
because ``metaforge.mcp.__main__`` built the MemoryClient but never
constructed an InsightStore alongside it. The G1 fix wires both —
including in-memory fallback when ``DATABASE_URL`` is unset, which is
exactly the path the in-process e2e fixture takes.

These tests assert the post-G1 contract: both memory tools accept
their canonical arguments and return a clean tool envelope. Empty
results are fine — the point is that the wire-up is complete.
"""

from __future__ import annotations

import pytest

from tests.integration.test_mcp_e2e._helpers import McpRpcError, call_tool

pytestmark = pytest.mark.asyncio


async def test_memory_list_insights_returns_clean_envelope(mcp_client):
    """``memory.list_insights`` no longer errors — returns an empty list.

    G1 regression guard: pre-fix this raised -32001. The in-process
    fixture builds the MCP server WITHOUT a populated insight store,
    so the expectation is "empty result, not error".
    """
    result = await call_tool(mcp_client, "memory.list_insights", {"limit": 10})
    # Tool returns ``{tool_id, status, data: {insights: [...]}, ...}``.
    # Pre-G1 we'd never reach this — call_tool would have raised
    # McpRpcError(-32001) on the missing insight_store.
    assert result.get("status") == "success", f"unexpected status: {result}"
    data = result.get("data", {})
    insights = data.get("insights")
    assert isinstance(insights, list)


async def test_memory_list_insights_honours_limit(mcp_client):
    """The ``limit`` argument is forwarded through to the store."""
    result = await call_tool(mcp_client, "memory.list_insights", {"limit": 1})
    assert result.get("status") == "success"
    insights = result.get("data", {}).get("insights", [])
    assert len(insights) <= 1


async def test_memory_retrieve_similar_experience_accepts_goal(mcp_client):
    """``memory.retrieve_similar_experience`` returns a clean envelope.

    Empty list is fine — the in-process fixture seeds no experiences.
    The point is to prove the surface is reachable + the schema flows
    through cleanly (catches a regression where the tool became
    unreachable, separate from semantic correctness).
    """
    try:
        result = await call_tool(
            mcp_client,
            "memory.retrieve_similar_experience",
            {"goal": "FEA stress validation on a bracket", "limit": 3},
        )
    except McpRpcError as exc:
        # G3 will fix pool contention against the live shared pool.
        # The in-process fixture uses an in-memory store so it shouldn't
        # hit that path — anything else is a real regression.
        if "another operation in progress" in str(exc.message):
            pytest.skip("G3 (pool contention) — out of scope for this test")
        raise
    assert result.get("status") == "success"
    # The adapter returns ``data.hits`` (per its declared output schema).
    hits = result.get("data", {}).get("hits")
    assert isinstance(hits, list)
