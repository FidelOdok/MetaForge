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


async def _tools_list_full(server) -> list[dict]:  # type: ignore[no-untyped-def]
    raw = await server.handle_request(
        json.dumps({"jsonrpc": "2.0", "id": "1", "method": "tools/list"})
    )
    response = json.loads(raw)
    return list(response["result"]["tools"])


class TestToolsListInventory:
    """Hard floor + must-have categories for the unified MCP server."""

    async def test_meets_minimum_inventory_with_twin_and_constraint(
        self, twin: InMemoryTwinAPI
    ) -> None:
        """With twin + constraint wired, the surface is ≥ 22 tools.

        Baseline (April 2026): cadquery=7 + freecad=5 + calculix=4
        + twin=5 + constraint=1 = 22. The knowledge-tools-with-service
        case is covered separately in
        ``test_knowledge_tools_registered_when_service_supplied`` so a
        regression in either path is caught independently.
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

    async def test_project_tools_registered_when_backend_supplied(
        self, twin: InMemoryTwinAPI
    ) -> None:
        """With a project backend wired (MET-427), the three project.* tools appear.

        Mirrors the twin/constraint regression — if a future bootstrap
        change forgets the ``project_backend`` kwarg, this catches it.
        """
        from api_gateway.projects.backend import InMemoryProjectBackend

        server = await build_unified_server(
            twin=twin,
            constraint_engine=twin.constraints,
            project_backend=InMemoryProjectBackend.create(),
        )
        names = await _tools_list(server)
        required = {"project.create", "project.list", "project.get"}
        missing = required - names
        assert not missing, f"project tools missing from tools/list: {missing}"
        # 22 baseline + 3 project = 25 floor with backend supplied.
        assert len(names) >= 25, f"surface shrank to {len(names)}: {sorted(names)}"

    async def test_knowledge_tools_registered_when_service_supplied(
        self, twin: InMemoryTwinAPI
    ) -> None:
        """With a knowledge_service wired (MET-433), all three knowledge
        tools appear on ``tools/list``.

        Same pattern as the twin + constraint + project regressions —
        if a future bootstrap change forgets the ``knowledge_service``
        kwarg, the standalone MCP loses ``knowledge.search`` /
        ``knowledge.ingest`` / ``knowledge.extract`` and this catches it.
        """
        from tests.unit._mcp_inventory_helpers import StubKnowledgeService

        server = await build_unified_server(
            twin=twin,
            constraint_engine=twin.constraints,
            knowledge_service=StubKnowledgeService(),
        )
        names = await _tools_list(server)
        required = {"knowledge.search", "knowledge.ingest", "knowledge.extract"}
        missing = required - names
        assert not missing, f"knowledge tools missing from tools/list: {missing}"
        # 22 baseline + 3 knowledge = 25 floor with knowledge supplied.
        assert len(names) >= 25, f"surface shrank to {len(names)}: {sorted(names)}"

    async def test_no_tool_has_top_level_union_in_input_schema(
        self, twin: InMemoryTwinAPI
    ) -> None:
        """Anthropic's tool-use API rejects `oneOf`/`allOf`/`anyOf` at the
        top level of `input_schema` with HTTP 400. A single offending tool
        kills the whole MCP surface from the client's perspective.

        Regression for MET-481: `project.get` shipped with a top-level
        `anyOf` ("must supply id or name"); Claude Code disconnected the
        whole MetaForge MCP every turn. Enforce the constraint across the
        full surface so no future adapter can re-introduce it.
        """
        from api_gateway.projects.backend import InMemoryProjectBackend
        from tests.unit._mcp_inventory_helpers import StubKnowledgeService

        server = await build_unified_server(
            twin=twin,
            constraint_engine=twin.constraints,
            project_backend=InMemoryProjectBackend.create(),
            knowledge_service=StubKnowledgeService(),
        )
        tools = await _tools_list_full(server)
        forbidden = ("oneOf", "allOf", "anyOf")
        offenders = [
            (t["name"], [k for k in forbidden if k in t.get("inputSchema", {})])
            for t in tools
            if any(k in t.get("inputSchema", {}) for k in forbidden)
        ]
        assert not offenders, (
            "MCP tools must not declare top-level oneOf/allOf/anyOf in inputSchema "
            f"(Anthropic API rejects them). Offenders: {offenders}"
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
