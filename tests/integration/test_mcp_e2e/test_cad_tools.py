"""CAD adapter readiness — cadquery / freecad / calculix in tools/list.

The MET-477 smoke surfaced that ``cadquery.*`` was missing from the
live MCP server's ``tools/list`` despite being in
``tool_registry/_ADAPTER_REGISTRY``. Root cause (G2):
``METAFORGE_ADAPTER_CADQUERY_URL`` was set in the gateway env, telling
bootstrap to fetch the adapter from a containerised endpoint that
isn't deployed in single-container setups. The fetch failed and the
adapter landed in ``failed`` instead of falling through to the
in-process implementation.

The G2 fix added the in-process fallback. These tests assert the
post-G2 contract: every CAD adapter that has an in-process impl is
present in the registry even when its remote URL env var points at a
dead container.
"""

from __future__ import annotations

import pytest

from tests.integration.test_mcp_e2e._helpers import rpc

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("adapter_id", ["cadquery", "freecad", "calculix"])
async def test_cad_adapter_registers_tools(mcp_client, adapter_id):
    """Each CAD adapter contributes at least one tool to tools/list.

    Parametrised so a regression in any one adapter shows up
    individually (a single test failure named after the broken
    adapter, not a generic "fewer tools than expected").
    """
    result = await rpc(mcp_client, "tools/list")
    names = [t["name"] for t in result.get("tools", [])]
    matches = [n for n in names if n.startswith(f"{adapter_id}.")]
    assert matches, (
        f"adapter {adapter_id!r} registered no tools — check bootstrap fallback path (G2)"
    )


async def test_cadquery_register_floor(mcp_client):
    """The cadquery adapter ships 7 tools; floor at the count we know.

    G2 specifically blocked the cadquery surface in the MET-477 smoke,
    so this test gets a tighter floor than the other CAD adapters
    (which weren't affected).
    """
    result = await rpc(mcp_client, "tools/list")
    cadquery_tools = [t for t in result.get("tools", []) if t["name"].startswith("cadquery.")]
    assert len(cadquery_tools) >= 5, (
        f"cadquery exposes only {len(cadquery_tools)} tools — "
        f"expected at least 5 (create_parametric, execute_script, "
        f"boolean_operation, get_properties, export_geometry, ...)"
    )
