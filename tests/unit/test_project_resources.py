"""The project resources, and what they must never imply (FORGE-355).

Five resources under ``metaforge://twin/<kind>/{project_id}``. Each renders
an existing source rather than computing anything new, so the tests are
mostly about the seams: that every kind is registered, that a matcher built
in a loop binds the right kind, and that an empty source says so rather than
saying nothing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from api_gateway.projects.brief import render_entities, render_hierarchy, render_requirements
from metaforge.mcp.server import UnifiedMcpServer
from tool_registry.tools.twin.adapter import TwinServer


@dataclass
class _Node:
    id: str
    name: str
    parent_id: str | None = None
    mass_kg: float | None = None
    cost: float | None = None


@dataclass
class _Row:
    requirement_name: str
    status: str
    evidence: list[Any] = field(default_factory=list)


@dataclass
class _Entity:
    title: str
    rationale: str | None = None
    alternatives: list[str] = field(default_factory=list)
    created_at: int = 0


class TestHierarchy:
    def test_empty_says_so_rather_than_nothing(self) -> None:
        # An empty string would render as a resource that exists and is
        # blank, which reads as a broken read rather than an empty project.
        assert "no product hierarchy" in render_hierarchy([]).lower()

    def test_children_nest_under_their_parent(self) -> None:
        text = render_hierarchy(
            [
                _Node(id="a", name="Arm", mass_kg=4.2),
                _Node(id="b", name="Base", parent_id="a", mass_kg=2.0),
            ]
        )
        arm = next(line for line in text.splitlines() if "Arm" in line)
        base = next(line for line in text.splitlines() if "Base" in line)
        assert base.startswith("  "), "child is not indented under its parent"
        assert not arm.startswith(" ")
        assert "4.2 kg" in arm


class TestRequirements:
    def test_empty_says_so(self) -> None:
        assert "no requirements" in render_requirements([]).lower()

    def test_no_data_is_reported_as_a_gap_not_a_pass(self) -> None:
        # F3's rule (FORGE-361) applied to the read side: an unverified
        # requirement must never read as a satisfied one.
        text = render_requirements(
            [_Row("Payload ≥ 1kg", "no_data"), _Row("Reach ≥ 600mm", "pass", evidence=[1])]
        )
        assert "no_data" in text
        assert "not a pass" in text
        assert "1 requirement(s) have no evidence" in text

    def test_a_fully_verified_project_is_not_nagged(self) -> None:
        text = render_requirements([_Row("Reach", "pass", evidence=[1])])
        assert "no evidence at all" not in text


class TestEntities:
    def test_empty_names_the_kind(self) -> None:
        assert "no risk entities" in render_entities([], kind="risk", title="Risks").lower()

    def test_rationale_and_alternatives_are_included(self) -> None:
        text = render_entities(
            [_Entity(title="Harmonic drive", rationale="Backlash", alternatives=["Cycloidal"])],
            kind="decision",
            title="Design decisions",
        )
        assert "Harmonic drive" in text
        assert "Backlash" in text
        assert "Cycloidal" in text


@pytest.mark.asyncio
class TestRegistration:
    async def _list(self, server: UnifiedMcpServer) -> list[dict[str, Any]]:
        raw = await server.handle_request(
            json.dumps(
                {"jsonrpc": "2.0", "id": 1, "method": "resources/templates/list", "params": {}}
            )
        )
        return json.loads(raw)["result"]["resourceTemplates"]

    async def test_every_declared_kind_is_registered(self) -> None:
        async def provider(kind: str, project_id: str) -> str:
            return f"{kind}:{project_id}"

        server = UnifiedMcpServer(adapters=[TwinServer(twin=None, brief_provider=provider)])
        templates = {r["uriTemplate"] for r in await self._list(server)}
        for kind in TwinServer.PROJECT_RESOURCES:
            assert f"metaforge://twin/{kind}/{{project_id}}" in templates

    async def test_each_matcher_binds_its_own_kind(self) -> None:
        # A closure over the loop variable would give every matcher the last
        # kind, so every read would resolve to "risks" and nobody would
        # notice until a brief came back as a risk list.
        seen: list[str] = []

        async def provider(kind: str, project_id: str) -> str:
            seen.append(kind)
            return kind

        server = UnifiedMcpServer(adapters=[TwinServer(twin=None, brief_provider=provider)])
        for kind in TwinServer.PROJECT_RESOURCES:
            raw = await server.handle_request(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "resources/read",
                        "params": {"uri": f"metaforge://twin/{kind}/p-1"},
                    }
                )
            )
            assert "error" not in json.loads(raw), kind
        assert seen == list(TwinServer.PROJECT_RESOURCES)

    async def test_an_unknown_kind_is_refused(self) -> None:
        async def provider(kind: str, project_id: str) -> str:
            return "should not be reached"

        server = UnifiedMcpServer(adapters=[TwinServer(twin=None, brief_provider=provider)])
        raw = await server.handle_request(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "resources/read",
                    "params": {"uri": "metaforge://twin/nonsense/p-1"},
                }
            )
        )
        assert "error" in json.loads(raw)
