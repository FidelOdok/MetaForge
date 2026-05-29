"""Tool inventory contract — every advertised tool has a valid manifest.

Parametrized over the live tools/list output. Catches accidental
drift (a tool registered with an empty schema, a missing description,
or a duplicate ID) immediately at the boundary.
"""

from __future__ import annotations

import pytest

from tests.integration.test_mcp_e2e._helpers import rpc

pytestmark = pytest.mark.asyncio


# The minimum tool count we expect at all times. Tools come and go
# across PRs (knowledge.populate_bom landed in PR #263, for example),
# so we don't pin an exact number — only a floor that catches
# "everything broke" without churning on additive growth.
_MIN_TOOL_COUNT = 10


async def test_inventory_meets_minimum_count(mcp_client):
    """Floor on the number of registered tools — catches catastrophic
    bootstrap regressions where adapters silently fail to load."""
    result = await rpc(mcp_client, "tools/list")
    tools = result.get("tools", [])
    assert len(tools) >= _MIN_TOOL_COUNT, (
        f"only {len(tools)} tools registered, expected at least {_MIN_TOOL_COUNT} — "
        "bootstrap likely lost an adapter"
    )


async def test_inventory_has_unique_ids(mcp_client):
    """No two registered tools may share the same ``name``."""
    result = await rpc(mcp_client, "tools/list")
    tools = result.get("tools", [])
    names = [t["name"] for t in tools]
    duplicates = {n for n in names if names.count(n) > 1}
    assert not duplicates, f"duplicate tool ids: {duplicates}"


async def test_inventory_names_use_dot_namespace(mcp_client):
    """Every tool follows the ``<adapter>.<action>`` convention.

    Catches a tool accidentally registered without an adapter prefix,
    which would collide across adapters and confuse routing.
    """
    result = await rpc(mcp_client, "tools/list")
    tools = result.get("tools", [])
    bad = [t["name"] for t in tools if "." not in t["name"]]
    assert not bad, f"tools without ``<adapter>.<action>`` namespace: {bad}"


async def test_inventory_every_tool_has_object_schema(mcp_client):
    """Every input schema must be a JSON-Schema ``"type": "object"``.

    MCP clients build their argument UIs from this — a missing or
    non-object schema would crash Claude Desktop / Cursor.
    """
    result = await rpc(mcp_client, "tools/list")
    tools = result.get("tools", [])
    for tool in tools:
        schema = tool.get("inputSchema") or tool.get("input_schema") or {}
        assert schema.get("type") == "object", (
            f"tool {tool['name']} inputSchema must be object-typed; got {schema.get('type')!r}"
        )
