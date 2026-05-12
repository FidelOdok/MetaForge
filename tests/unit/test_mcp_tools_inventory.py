"""Regression test for the unified MCP tool surface (MET-426).

Pins a floor on `tools/list` so that adapter bootstrap regressions
(MET-420, MET-421) can't silently drop half the tool surface again.
Both prior breakages — the `tools/call` argument-mapping bug and the
missing twin/constraint wire-up — would have been caught here.
"""

from __future__ import annotations

import json

import pytest

from metaforge.mcp.server import build_unified_server
from twin_core.api import InMemoryTwinAPI


@pytest.fixture
async def twin() -> InMemoryTwinAPI:
    return InMemoryTwinAPI.create()


async def _tools_list(server) -> set[str]:  # type: ignore[no-untyped-def]
    raw = await server.handle_request(
        json.dumps({"jsonrpc": "2.0", "id": "1", "method": "tools/list"})
    )
    response = json.loads(raw)
    return {tool["name"] for tool in response["result"]["tools"]}


class TestToolsListInventory:
    """Hard floor + must-have categories for the unified MCP server."""

    async def test_meets_minimum_inventory_with_twin_and_constraint(
        self, twin: InMemoryTwinAPI
    ) -> None:
        """With twin + constraint wired, the surface is ≥ 22 tools.

        Baseline (April 2026): cadquery=7 + freecad=5 + calculix=4
        + twin=5 + constraint=1 = 22. When MET-422 (knowledge.extract)
        lands the floor moves to 23+, and this assert will still hold.
        """
        server = await build_unified_server(
            twin=twin,
            constraint_engine=twin.constraints,
        )
        names = await _tools_list(server)
        assert len(names) >= 22, (
            f"tools/list shrank to {len(names)} tools — adapter bootstrap regression. "
            f"Present: {sorted(names)}"
        )

    async def test_twin_tools_registered(self, twin: InMemoryTwinAPI) -> None:
        """All five twin tools must appear.

        Regression for MET-421 — these silently disappeared because
        the gateway and standalone-MCP entrypoints failed to pass
        `twin=` into `bootstrap_tool_registry`.
        """
        server = await build_unified_server(
            twin=twin,
            constraint_engine=twin.constraints,
        )
        names = await _tools_list(server)
        required = {
            "twin.get_node",
            "twin.thread_for",
            "twin.find_by_property",
            "twin.constraint_violations",
            "twin.query_cypher",
        }
        missing = required - names
        assert not missing, f"twin tools missing from tools/list: {missing}"

    async def test_constraint_validate_registered(self, twin: InMemoryTwinAPI) -> None:
        """`constraint.validate` must appear when constraint_engine is supplied.

        Regression for MET-421 — this tool also vanished when the
        constraint engine wasn't wired through bootstrap.
        """
        server = await build_unified_server(
            twin=twin,
            constraint_engine=twin.constraints,
        )
        names = await _tools_list(server)
        assert "constraint.validate" in names, (
            f"constraint.validate missing from tools/list; present: {sorted(names)}"
        )

    async def test_no_twin_yields_smaller_surface(self) -> None:
        """Omitting twin/constraint drops them — the regression test must
        actually catch the contrapositive, otherwise it's tautological.
        """
        server_with = await build_unified_server(
            twin=InMemoryTwinAPI.create(),
            constraint_engine=InMemoryTwinAPI.create().constraints,
        )
        server_without = await build_unified_server()

        with_names = await _tools_list(server_with)
        without_names = await _tools_list(server_without)

        # Without twin/constraint, the 5 twin tools and constraint.validate
        # must NOT appear — otherwise the wire-up isn't actually conditional.
        assert "twin.get_node" not in without_names
        assert "constraint.validate" not in without_names
        # And the surface should shrink by at least 6 tools.
        assert len(with_names) - len(without_names) >= 6
